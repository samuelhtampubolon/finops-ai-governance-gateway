"""Unit and integration tests for Semantic Cache and Complexity Routing."""

import sys
import time
from pathlib import Path
import pytest
import numpy as np

# Ensure repo root is in python path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.config import settings
from src.database import FinOpsDB, get_db_connection
from src.services.complexity_router import complexity_router_service
from src.services.semantic_cache import VectorEmbeddingEngine, SemanticCacheService


@pytest.fixture(scope="module", autouse=True)
def setup_test_db():
    """Ensure database connection and schema are ready."""
    get_db_connection()
    FinOpsDB.upsert_tenant(
        tenant_id="test-tenant-alpha",
        tenant_name="Tenant Alpha",
        plan_tier="enterprise",
        monthly_mrr_usd=10000.0,
        monthly_token_budget_usd=2000.0
    )
    FinOpsDB.upsert_tenant(
        tenant_id="test-tenant-beta",
        tenant_name="Tenant Beta",
        plan_tier="pro",
        monthly_mrr_usd=1500.0,
        monthly_token_budget_usd=300.0
    )


def test_vector_embedding_and_cosine_similarity():
    """Test deterministic vector embedding normalization and similarity properties."""
    engine = VectorEmbeddingEngine(dimensions=128)

    vec1 = engine.embed("How do I configure Single Sign-On with SAML 2.0?")
    vec2 = engine.embed("How do I configure Single Sign-On (SSO) with SAML 2.0?")
    vec3 = engine.embed("What is the recipe for chocolate chip cookies?")

    # L2 unit normalization check
    assert np.isclose(np.linalg.norm(vec1), 1.0, atol=1e-5)
    assert np.isclose(np.linalg.norm(vec2), 1.0, atol=1e-5)

    # Semantic similarity check
    sim_similar = engine.cosine_similarity(vec1, vec2)
    sim_unrelated = engine.cosine_similarity(vec1, vec3)

    assert sim_similar > 0.85, f"Expected high similarity for SSO prompts, got {sim_similar}"
    assert sim_similar > sim_unrelated, f"Similar ({sim_similar}) should exceed unrelated ({sim_unrelated})"


def test_sub_20ms_semantic_cache_lookup():
    """Test that semantic cache hits execute in well under 20ms."""
    cache = SemanticCacheService(similarity_threshold=0.90)
    tenant_id = "test-tenant-alpha"
    prompt = "Where can I view my monthly invoice and cloud bill?"
    response = "You can view your invoices under Billing > Invoices in the portal."

    # Store entry
    cache.store(
        tenant_id=tenant_id,
        prompt_text=prompt,
        response_text=response,
        routed_model="local-test",
        prompt_tokens=20,
        completion_tokens=30
    )

    # Perform repeated lookups and verify latency SLA
    latencies = []
    for _ in range(10):
        matched, sim, lat_ms = cache.lookup(tenant_id, prompt)
        latencies.append(lat_ms)

    avg_lat = sum(latencies) / len(latencies)
    assert matched is not None, "Cache lookup should hit"
    assert matched["response_text"] == response
    assert sim >= 0.90
    assert avg_lat < 20.0, f"Cache latency SLA breached: {avg_lat:.2f}ms >= 20ms"


def test_cross_tenant_cache_isolation():
    """Verify Rule 3 & OWASP API1: Tenant A's cached entries MUST NEVER leak to Tenant B."""
    cache = SemanticCacheService(similarity_threshold=0.90)
    tenant_a = "test-tenant-alpha"
    tenant_b = "test-tenant-beta"

    secret_prompt = "What is the private encryption key password for database cluster?"
    private_response = "The private key password is SuperSecretCompanyPassword9988"

    # Tenant A stores completion
    cache.store(
        tenant_id=tenant_a,
        prompt_text=secret_prompt,
        response_text=private_response,
        routed_model="frontier-test",
        prompt_tokens=15,
        completion_tokens=20
    )

    # Tenant A queries -> Cache Hit
    match_a, sim_a, _ = cache.lookup(tenant_id=tenant_a, prompt_text=secret_prompt)
    assert match_a is not None, "Tenant A should get a cache hit on its own query"
    assert match_a["response_text"] == private_response

    # Tenant B queries the EXACT SAME prompt -> Cache MISS (Isolation Enforced)
    match_b, sim_b, _ = cache.lookup(tenant_id=tenant_b, prompt_text=secret_prompt)
    assert match_b is None, "Tenant B MUST NOT access Tenant A's private cache partition!"


def test_complexity_router_routine_vs_frontier():
    """Verify FR-03: Route routine tasks to local Ollama and high-reasoning tasks to Frontier."""
    # Routine query -> Local Ollama
    routine_prompt = "Classify the sentiment of this support ticket: The app crashed twice today."
    score_routine, model_routine, factors_routine = complexity_router_service.evaluate(routine_prompt)

    assert score_routine < settings.COMPLEXITY_ROUTING_THRESHOLD, f"Expected low score, got {score_routine}"
    assert model_routine == settings.LOCAL_MODEL_NAME

    # High complexity query -> Frontier
    complex_prompt = (
        "Analyze the algorithmic complexity step by step and compare the tradeoffs between Raft consensus "
        "and Paxos in a distributed transactional key-value store with formal mathematical proof and edge cases."
    )
    score_complex, model_complex, factors_complex = complexity_router_service.evaluate(complex_prompt)

    assert score_complex >= settings.COMPLEXITY_ROUTING_THRESHOLD, f"Expected high score, got {score_complex}"
    assert model_complex == settings.FRONTIER_MODEL_NAME
