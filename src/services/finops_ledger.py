"""FinOps unit economics ledger, real-time COGS accounting, and budget circuit breakers."""

from typing import Any, Dict, Optional, Tuple
from src.config import settings
from src.database import FinOpsDB


class FinOpsLedgerService:
    """Manages unit economics calculations, COGS attribution, and budget circuit breakers."""

    @staticmethod
    def calculate_cogs_and_savings(
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        is_cache_hit: bool
    ) -> Tuple[float, float]:
        """Calculate Cost of Goods Sold (COGS) and savings in USD for an inference request.

        Governing Equation:
        request_cogs_usd = (prompt_tokens / 1000 * input_rate) + (completion_tokens / 1000 * output_rate) + overhead_cogs
        """
        overhead_cogs = settings.DEFAULT_OVERHEAD_COGS_USD

        # Baseline frontier cost that would have been incurred without caching/local routing
        frontier_input = prompt_tokens / 1000.0 * settings.FRONTIER_INPUT_COST_PER_1K
        frontier_output = completion_tokens / 1000.0 * settings.FRONTIER_OUTPUT_COST_PER_1K
        hypothetical_frontier_cogs = frontier_input + frontier_output + overhead_cogs

        if is_cache_hit:
            # Semantic cache hits only incur gateway overhead ($0.00 token cost)
            actual_cogs = overhead_cogs
            savings_usd = max(0.0, hypothetical_frontier_cogs - actual_cogs)
            return round(actual_cogs, 6), round(savings_usd, 6)

        if model_name == settings.LOCAL_MODEL_NAME:
            local_input = prompt_tokens / 1000.0 * settings.LOCAL_INPUT_COST_PER_1K
            local_output = completion_tokens / 1000.0 * settings.LOCAL_OUTPUT_COST_PER_1K
            actual_cogs = local_input + local_output + overhead_cogs
            savings_usd = max(0.0, hypothetical_frontier_cogs - actual_cogs)
            return round(actual_cogs, 6), round(savings_usd, 6)

        # Frontier model invocation
        actual_cogs = hypothetical_frontier_cogs
        savings_usd = 0.0
        return round(actual_cogs, 6), round(savings_usd, 6)

    @staticmethod
    def check_budget_circuit_breaker(
        tenant_id: str,
        estimated_cost_usd: float = 0.005,
        target_model: str = settings.FRONTIER_MODEL_NAME
    ) -> Tuple[bool, bool, str, float]:
        """Check if tenant has sufficient token budget remaining before routing request.

        Returns:
            Tuple of (is_allowed: bool, downgrade_to_local: bool, reason: str, remaining_budget: float)
        """
        tenant = FinOpsDB.get_tenant(tenant_id)
        if not tenant:
            # Auto-provision standard tier if unknown
            FinOpsDB.upsert_tenant(
                tenant_id=tenant_id,
                tenant_name=f"Tenant {tenant_id}",
                plan_tier="pro",
                monthly_mrr_usd=1000.0,
                monthly_token_budget_usd=250.0,
                current_month_spend_usd=0.0,
                enforce_hard_cap=True
            )
            return True, False, "Tenant auto-initialized with standard budget", 250.0

        current_spend = tenant["current_month_spend_usd"]
        budget = tenant["monthly_token_budget_usd"]
        remaining = max(0.0, budget - current_spend)
        enforce_hard_cap = tenant["enforce_hard_cap"]

        # If budget exhausted
        if current_spend >= budget:
            if enforce_hard_cap:
                return False, False, f"Monthly token budget of ${budget:.2f} fully exhausted. Hard cap circuit breaker tripped.", 0.0
            else:
                # Soft cap: allow downgrade to local model to keep service alive
                return True, True, "Budget exceeded; downgraded to local low-cost model.", 0.0

        # If nearing budget and requesting frontier model, check headroom
        if (current_spend + estimated_cost_usd) > budget and target_model == settings.FRONTIER_MODEL_NAME:
            if enforce_hard_cap:
                # Downgrade to local model instead of failing outright if local cost fits
                return True, True, "Frontier request exceeds remaining budget; automatically downgraded to local model.", remaining

        return True, False, "Budget verification passed", remaining

    @staticmethod
    def record_transaction(
        req_id: str,
        tenant_id: str,
        feature_tag: str,
        routed_model: str,
        complexity_score: float,
        cache_hit: bool,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float
    ) -> Dict[str, Any]:
        """Atomically record inference COGS in DuckDB ledger and update tenant spend."""
        cogs_usd, savings_usd = FinOpsLedgerService.calculate_cogs_and_savings(
            model_name=routed_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            is_cache_hit=cache_hit
        )

        # Log into DuckDB time-series telemetry table
        FinOpsDB.log_telemetry(
            req_id=req_id,
            tenant_id=tenant_id,
            feature_tag=feature_tag,
            routed_model=routed_model,
            complexity_score=complexity_score,
            cache_hit=cache_hit,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cogs_usd=cogs_usd,
            savings_usd=savings_usd,
            latency_ms=latency_ms
        )

        # Update tenant spend
        new_spend = FinOpsDB.increment_tenant_spend(tenant_id, cogs_usd)
        tenant = FinOpsDB.get_tenant(tenant_id)
        remaining_budget = max(0.0, tenant["monthly_token_budget_usd"] - new_spend) if tenant else 0.0

        return {
            "cogs_usd": cogs_usd,
            "savings_usd": savings_usd,
            "current_spend_usd": round(new_spend, 4),
            "remaining_budget_usd": round(remaining_budget, 4)
        }


finops_ledger_service = FinOpsLedgerService()
