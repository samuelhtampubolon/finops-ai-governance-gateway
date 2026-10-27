"""Configuration management for FinOps & Hybrid AI Resource Governance Gateway."""

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    """Application and infrastructure runtime settings."""

    # Gateway Server
    GATEWAY_HOST: str = "0.0.0.0"
    GATEWAY_PORT: int = 8000
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Streamlit UI
    STREAMLIT_PORT: int = 8501

    # Security & JWT Authentication
    JWT_SECRET_KEY: str = "finops-super-secure-production-ready-secret-key-9988-jwt-token"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 1440  # 24 hours

    # DuckDB Persistence
    DUCKDB_PATH: str = "data/finops_ledger.duckdb"

    # Semantic Cache Parameters
    SEMANTIC_SIMILARITY_THRESHOLD: float = 0.92
    CACHE_VECTOR_DIMENSIONS: int = 128
    DEFAULT_OVERHEAD_COGS_USD: float = 0.00005  # $0.05 per 1,000 cached lookups compute overhead

    # Unit Economics Pricing (USD per 1,000 tokens)
    # Local tier: e.g. Ollama Llama 3 running on on-prem / reserved hardware
    LOCAL_MODEL_NAME: str = "local-ollama-llama3"
    LOCAL_INPUT_COST_PER_1K: float = 0.00010   # $0.10 / 1M input tokens
    LOCAL_OUTPUT_COST_PER_1K: float = 0.00020  # $0.20 / 1M output tokens

    # Frontier tier: e.g. GPT-4o / Claude 3.5 Sonnet / GPT-5 Frontier APIs
    FRONTIER_MODEL_NAME: str = "frontier-gpt5-claude"
    FRONTIER_INPUT_COST_PER_1K: float = 0.0150  # $15.00 / 1M input tokens (150x local!)
    FRONTIER_OUTPUT_COST_PER_1K: float = 0.0300 # $30.00 / 1M output tokens

    # Hybrid Routing Threshold (0.0 to 1.0)
    COMPLEXITY_ROUTING_THRESHOLD: float = 0.65

    # Target Gross Margin Threshold
    DEFAULT_TARGET_GROSS_MARGIN_PCT: float = 75.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def resolved_db_path(self) -> str:
        """Resolve DuckDB file path relative to repo root."""
        base_dir = Path(__file__).resolve().parent.parent
        db_path = Path(self.DUCKDB_PATH)
        if not db_path.is_absolute():
            db_path = base_dir / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return str(db_path)


settings = GatewaySettings()
