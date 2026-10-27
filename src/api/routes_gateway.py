"""API route handlers for FinOps Gateway, Chat Completions, Auth, and Unit Economics."""

import time
import uuid
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.config import settings
from src.database import FinOpsDB
from src.schemas import (
    ChatChoice,
    ChatMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    FinOpsMetadata,
    TenantUnitEconomicsResponse,
    TokenAuthRequest,
    TokenAuthResponse,
    UsageInfo,
)
from src.security import (
    create_tenant_jwt,
    get_current_tenant,
    inspect_prompt_injection,
    redact_pii,
)
from src.services.complexity_router import complexity_router_service
from src.services.finops_ledger import finops_ledger_service
from src.services.semantic_cache import semantic_cache_service

router = APIRouter()


# ==============================================================================
# AUTHENTICATION & CREDENTIAL PROVISIONING
# ==============================================================================

@router.post(
    "/auth/token",
    response_model=TokenAuthResponse,
    summary="Issue Tenant API JWT",
    tags=["Authentication"]
)
async def issue_tenant_token(payload: TokenAuthRequest) -> TokenAuthResponse:
    """Generate signed JWT token for tenant authentication with multi-tenant scoping."""
    # Ensure tenant exists in database
    existing = FinOpsDB.get_tenant(payload.tenant_id)
    if not existing:
        FinOpsDB.upsert_tenant(
            tenant_id=payload.tenant_id,
            tenant_name=payload.tenant_name or f"Org {payload.tenant_id}",
            plan_tier=payload.plan_tier or "enterprise",
            monthly_mrr_usd=5000.0,
            monthly_token_budget_usd=1000.0,
            current_month_spend_usd=0.0,
            enforce_hard_cap=True
        )

    token = create_tenant_jwt(
        tenant_id=payload.tenant_id,
        plan_tier=payload.plan_tier or "enterprise"
    )
    return TokenAuthResponse(
        access_token=token,
        token_type="bearer",
        expires_in_minutes=settings.JWT_EXPIRATION_MINUTES,
        tenant_id=payload.tenant_id,
        plan_tier=payload.plan_tier or "enterprise"
    )


# ==============================================================================
# UNIFIED FINOPS INFERENCE GATEWAY
# ==============================================================================

@router.post(
    "/gateway/chat/completions",
    response_model=ChatCompletionResponse,
    summary="Execute Governed Inference Request",
    tags=["Gateway"]
)
async def create_chat_completion(
    request: ChatCompletionRequest,
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
) -> ChatCompletionResponse:
    """Intercept inference request with prompt firewall, PII redaction, semantic caching,

    complexity-based model routing, and DuckDB unit economics attribution.
    """
    req_start = time.perf_counter()
    tenant_id = current_tenant["tenant_id"]

    # 1. Parse conversational turns
    system_prompts = [m.content for m in request.messages if m.role == "system"]
    user_prompts = [m.content for m in request.messages if m.role == "user"]

    system_text = "\n".join(system_prompts)
    raw_user_text = user_prompts[-1] if user_prompts else ""

    if not raw_user_text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prompt cannot be empty."
        )

    # 2. OWASP LLM01: Prompt Injection & Jailbreak Firewall
    is_injection, injection_score, flag_reason = inspect_prompt_injection(raw_user_text)
    if is_injection:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Prompt Security Violation (OWASP LLM01)",
                "reason": flag_reason,
                "injection_confidence": injection_score,
                "action": "Blocked before model routing to protect tenant perimeter."
            }
        )

    # 3. OWASP LLM06: PII Redaction
    sanitized_prompt, redacted_categories = redact_pii(raw_user_text)
    pii_was_redacted = len(redacted_categories) > 0

    # 4. Semantic Cache Check (sub-20ms target, $0.00 token cost)
    if not request.force_refresh:
        cached_entry, sim_score, lookup_lat = semantic_cache_service.lookup(
            tenant_id=tenant_id,
            prompt_text=sanitized_prompt
        )
        if cached_entry is not None:
            total_latency = (time.perf_counter() - req_start) * 1000.0
            req_id = f"req-{uuid.uuid4().hex[:12]}"

            p_tokens = cached_entry["prompt_tokens"]
            c_tokens = cached_entry["completion_tokens"]

            # Record cache hit transaction
            finops_result = finops_ledger_service.record_transaction(
                req_id=req_id,
                tenant_id=tenant_id,
                feature_tag=request.feature_tag,
                routed_model="semantic-cache-hit",
                complexity_score=0.10,
                cache_hit=True,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                latency_ms=total_latency
            )

            return ChatCompletionResponse(
                id=req_id,
                created=int(time.time()),
                model=f"semantic-cache-hit (source: {cached_entry['routed_model']})",
                choices=[
                    ChatChoice(
                        index=0,
                        message=ChatMessage(role="assistant", content=cached_entry["response_text"]),
                        finish_reason="stop"
                    )
                ],
                usage=UsageInfo(
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    total_tokens=p_tokens + c_tokens
                ),
                finops=FinOpsMetadata(
                    cache_hit=True,
                    latency_ms=round(total_latency, 2),
                    cogs_usd=finops_result["cogs_usd"],
                    savings_usd=finops_result["savings_usd"],
                    routed_model="semantic-cache-hit",
                    complexity_score=0.10,
                    pii_redacted=pii_was_redacted,
                    redacted_categories=redacted_categories,
                    tenant_id=tenant_id,
                    remaining_budget_usd=finops_result["remaining_budget_usd"]
                )
            )

    # 5. Hybrid Complexity Routing (Local vs Frontier)
    complexity_score, routed_model, decision_factors = complexity_router_service.evaluate(
        prompt=sanitized_prompt,
        system_prompt=system_text
    )

    # 6. FinOps Budget Circuit Breaker Check
    estimated_cost = (
        0.015 if routed_model == settings.FRONTIER_MODEL_NAME else 0.0002
    )
    is_allowed, downgrade, breaker_reason, remaining_budget = finops_ledger_service.check_budget_circuit_breaker(
        tenant_id=tenant_id,
        estimated_cost_usd=estimated_cost,
        target_model=routed_model
    )

    if not is_allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "FinOps Circuit Breaker Tripped (Denial of Wallet Protection)",
                "reason": breaker_reason,
                "tenant_id": tenant_id,
                "remaining_budget_usd": remaining_budget,
                "action": "Inference rejected to preserve target Gross Margin (>75%)."
            }
        )

    if downgrade:
        routed_model = settings.LOCAL_MODEL_NAME

    # 7. Local/Frontier Inference Execution
    # Simulate low-latency response generation deterministically for local/offline run
    prompt_tokens = max(10, len(sanitized_prompt.split()) * 2)
    completion_text = _generate_local_inference(sanitized_prompt, routed_model)
    completion_tokens = max(15, len(completion_text.split()) * 2)

    total_latency = (time.perf_counter() - req_start) * 1000.0
    req_id = f"req-{uuid.uuid4().hex[:12]}"

    # 8. Record Transaction in DuckDB Ledger
    finops_result = finops_ledger_service.record_transaction(
        req_id=req_id,
        tenant_id=tenant_id,
        feature_tag=request.feature_tag,
        routed_model=routed_model,
        complexity_score=complexity_score,
        cache_hit=False,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=total_latency
    )

    # 9. Store in Tenant's Semantic Cache for Future Requests
    semantic_cache_service.store(
        tenant_id=tenant_id,
        prompt_text=sanitized_prompt,
        response_text=completion_text,
        routed_model=routed_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens
    )

    return ChatCompletionResponse(
        id=req_id,
        created=int(time.time()),
        model=routed_model,
        choices=[
            ChatChoice(
                index=0,
                message=ChatMessage(role="assistant", content=completion_text),
                finish_reason="stop"
            )
        ],
        usage=UsageInfo(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens
        ),
        finops=FinOpsMetadata(
            cache_hit=False,
            latency_ms=round(total_latency, 2),
            cogs_usd=finops_result["cogs_usd"],
            savings_usd=finops_result["savings_usd"],
            routed_model=routed_model,
            complexity_score=complexity_score,
            pii_redacted=pii_was_redacted,
            redacted_categories=redacted_categories,
            tenant_id=tenant_id,
            remaining_budget_usd=finops_result["remaining_budget_usd"]
        )
    )


# ==============================================================================
# FINOPS UNIT ECONOMICS & METRICS REPORTING
# ==============================================================================

@router.get(
    "/finops/unit-economics",
    response_model=TenantUnitEconomicsResponse,
    summary="Get Tenant-Scoped Unit Economics & Margin",
    tags=["FinOps Analytics"]
)
async def get_tenant_unit_economics(
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
) -> TenantUnitEconomicsResponse:
    """Return real-time Cost of Goods Sold (COGS), Gross Margin %, and cache deflection savings."""
    tenant_id = current_tenant["tenant_id"]
    metrics = FinOpsDB.get_unit_economics_for_tenant(tenant_id)
    if not metrics:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant '{tenant_id}' has no recorded ledger history."
        )

    is_exhausted = metrics["current_month_spend_usd"] >= metrics["monthly_token_budget_usd"]
    return TenantUnitEconomicsResponse(
        **metrics,
        budget_exhausted=is_exhausted
    )


@router.get(
    "/finops/tenants",
    summary="List All Tenants and Budget Status",
    tags=["FinOps Analytics"]
)
async def list_tenants() -> List[Dict[str, Any]]:
    """List all registered platform tenants with real-time budget utilization."""
    tenants = FinOpsDB.get_all_tenants()
    results = []
    for t in tenants:
        metrics = FinOpsDB.get_unit_economics_for_tenant(t["tenant_id"])
        if metrics:
            results.append(metrics)
        else:
            results.append(t)
    return results


@router.post(
    "/finops/reset-cache",
    summary="Flush Tenant Cache Partition",
    tags=["FinOps Administration"]
)
async def reset_tenant_cache(
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
) -> Dict[str, str]:
    """Flush the in-memory cache partition for the authenticated tenant."""
    tenant_id = current_tenant["tenant_id"]
    semantic_cache_service.clear_tenant_cache(tenant_id)
    return {"status": "success", "message": f"Semantic cache partition for '{tenant_id}' flushed."}


def _generate_local_inference(prompt: str, model_name: str) -> str:
    """Deterministic simulated reasoning generator for offline local runs."""
    p_lower = prompt.lower()
    if "sso" in p_lower or "okta" in p_lower:
        return ("To configure SSO: Navigate to Admin Portal > Security > SSO. "
                "Download your Identity Provider (IdP) SAML metadata and paste the ACS URL into Okta.")
    elif "sql" in p_lower or "query" in p_lower:
        return ("SELECT tenant_id, COUNT(*) AS request_count, SUM(cogs_usd) AS total_cogs "
                "FROM finops_telemetry_logs GROUP BY 1 ORDER BY total_cogs DESC;")
    elif "classify" in p_lower or "sentiment" in p_lower:
        return "Classification: Positive (Intent: Informational Inquiry, Urgency: Low)"
    elif "python" in p_lower or "code" in p_lower:
        return ("def calculate_gross_margin(mrr: float, cogs: float) -> float:\n"
                "    return ((mrr - cogs) / mrr) * 100.0 if mrr > 0 else 0.0")
    else:
        return (f"Generated completion via {model_name}. Analysis concluded successfully for prompt: "
                f"'{prompt[:60]}...' with optimal unit economics routing.")
