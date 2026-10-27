"""Pydantic v2 schemas and data validation models for FinOps AI Governance Gateway."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """Individual conversational turn message."""
    role: str = Field(..., description="Role of the author: system, user, assistant")
    content: str = Field(..., description="Content of the message")

    model_config = ConfigDict(extra="forbid")


class ChatCompletionRequest(BaseModel):
    """Standardized OpenAI-compatible inference request intercepted by the FinOps gateway."""
    messages: List[ChatMessage] = Field(..., min_length=1, description="List of messages comprising the conversation")
    feature_tag: str = Field(default="general_inference", description="Tag identifying the requesting SaaS microservice / product feature")
    max_tokens: Optional[int] = Field(default=512, ge=1, le=4096, description="Maximum tokens for completion")
    temperature: Optional[float] = Field(default=0.7, ge=0.0, le=2.0, description="Sampling temperature")
    force_refresh: Optional[bool] = Field(default=False, description="Bypass semantic cache lookup if true")

    model_config = ConfigDict(extra="forbid")


class FinOpsMetadata(BaseModel):
    """Cost of Goods Sold (COGS) and governance telemetry attached to every response."""
    cache_hit: bool = Field(..., description="Whether response was served from semantic cache (<20ms)")
    latency_ms: float = Field(..., description="Total roundtrip gateway processing latency in milliseconds")
    cogs_usd: float = Field(..., description="Exact compute and token cost incurred by this request in USD")
    savings_usd: float = Field(..., description="USD saved via semantic cache deflection or local model routing")
    routed_model: str = Field(..., description="Model that served the request (cache, local-ollama, or frontier)")
    complexity_score: float = Field(..., description="Calculated query complexity score between 0.0 and 1.0")
    pii_redacted: bool = Field(..., description="Whether PII was scrubbed from the ingress prompt")
    redacted_categories: List[str] = Field(default_factory=list, description="Categories of PII detected and sanitized")
    tenant_id: str = Field(..., description="Tenant attribution ID")
    remaining_budget_usd: float = Field(..., description="Remaining monthly token budget for this tenant")

    model_config = ConfigDict(extra="forbid")


class UsageInfo(BaseModel):
    """Token usage breakdown."""
    prompt_tokens: int = Field(..., description="Tokens in input prompt")
    completion_tokens: int = Field(..., description="Tokens in generated response")
    total_tokens: int = Field(..., description="Sum of prompt and completion tokens")

    model_config = ConfigDict(extra="forbid")


class ChatChoice(BaseModel):
    """Single completion candidate."""
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"

    model_config = ConfigDict(extra="forbid")


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible inference response augmented with FinOps unit economics metadata."""
    id: str = Field(..., description="Unique request identifier")
    object: str = "chat.completion"
    created: int = Field(..., description="Unix timestamp of completion")
    model: str = Field(..., description="Final model selected and routed")
    choices: List[ChatChoice] = Field(..., description="Generated chat choices")
    usage: UsageInfo = Field(..., description="Token consumption metrics")
    finops: FinOpsMetadata = Field(..., description="FinOps governance telemetry")

    model_config = ConfigDict(extra="forbid")


class TokenAuthRequest(BaseModel):
    """Request payload for issuing tenant JWT credentials."""
    tenant_id: str = Field(..., min_length=2, description="Unique tenant identifier")
    tenant_name: Optional[str] = Field(default=None, description="Human-readable organization name")
    plan_tier: Optional[str] = Field(default="enterprise", description="Subscription tier: enterprise, pro, free")

    model_config = ConfigDict(extra="forbid")


class TokenAuthResponse(BaseModel):
    """Response containing signed JWT bearer token."""
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    tenant_id: str
    plan_tier: str

    model_config = ConfigDict(extra="forbid")


class TenantUnitEconomicsResponse(BaseModel):
    """Comprehensive unit economics and margin metrics for a tenant."""
    tenant_id: str
    tenant_name: str
    plan_tier: str
    monthly_mrr_usd: float
    monthly_token_budget_usd: float
    current_month_spend_usd: float
    total_requests: int
    total_cogs_usd: float
    total_savings_usd: float
    gross_margin_pct: float
    cache_hit_rate_pct: float
    avg_latency_ms: float
    total_tokens: int
    enforce_hard_cap: bool
    budget_exhausted: bool = False

    model_config = ConfigDict(extra="ignore")
