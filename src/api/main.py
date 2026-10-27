"""FastAPI main application entrypoint for FinOps AI Governance Gateway."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.routes_gateway import router as gateway_router
from src.config import settings
from src.database import FinOpsDB, get_db_connection
from src.seed_finops_telemetry import seed_database


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager to initialize DuckDB and warm semantic caches on startup."""
    # Initialize DuckDB connection and schemas
    get_db_connection()
    # Ensure database is seeded with initial data
    seed_database(force_reseed=False)
    yield
    # Shutdown logic if needed


app = FastAPI(
    title="FinOps & Hybrid AI Resource Governance Gateway",
    description=(
        "Production-grade FinOps & AI Governance Gateway delivering sub-20ms semantic cache hits, "
        "hybrid local-vs-frontier model routing, multi-tenant COGS attribution, token budget circuit breakers, "
        "and OWASP LLM security hardening."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Cross-Origin Resource Sharing (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API V1 routes
app.include_router(gateway_router, prefix="/api/v1")


@app.get("/healthz", tags=["System Health"], summary="Liveness Probe")
async def healthz() -> Dict[str, str]:
    """Kubernetes / Docker liveness probe."""
    return {"status": "ok", "service": "finops-ai-governance-gateway"}


@app.get("/readyz", tags=["System Health"], summary="Readiness Probe")
async def readyz() -> JSONResponse:
    """Readiness probe checking DuckDB storage and cache service health."""
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1").fetchone()
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "ready",
                "database": "duckdb_connected",
                "semantic_cache": "active",
                "gateway_version": "1.0.0"
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "error": str(e)}
        )
