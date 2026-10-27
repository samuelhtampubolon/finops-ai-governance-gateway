import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

# Ensure repo root is in python path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import numpy as np

from src.config import settings
from src.database import FinOpsDB, get_db_connection
from src.services.semantic_cache import semantic_cache_service

TENANTS_SEED_DATA = [
    {
        "tenant_id": "tenant-acme-corp",
        "tenant_name": "Acme Global Enterprise",
        "plan_tier": "enterprise",
        "monthly_mrr_usd": 12500.0,
        "monthly_token_budget_usd": 2500.0,
        "enforce_hard_cap": True
    },
    {
        "tenant_id": "tenant-globex-ai",
        "tenant_name": "Globex AI Cloud Solutions",
        "plan_tier": "enterprise",
        "monthly_mrr_usd": 8000.0,
        "monthly_token_budget_usd": 1600.0,
        "enforce_hard_cap": True
    },
    {
        "tenant_id": "tenant-nexus-tech",
        "tenant_name": "NexusTech SaaS",
        "plan_tier": "pro",
        "monthly_mrr_usd": 1200.0,
        "monthly_token_budget_usd": 250.0,
        "enforce_hard_cap": False
    },
    {
        "tenant_id": "tenant-strata-io",
        "tenant_name": "Strata Data Systems",
        "plan_tier": "pro",
        "monthly_mrr_usd": 950.0,
        "monthly_token_budget_usd": 200.0,
        "enforce_hard_cap": True
    },
    {
        "tenant_id": "tenant-free-trial",
        "tenant_name": "Developer Sandbox Tier",
        "plan_tier": "free",
        "monthly_mrr_usd": 50.0,
        "monthly_token_budget_usd": 15.0,
        "enforce_hard_cap": True
    }
]

FREQUENT_PROMPTS = [
    ("How do I configure Single Sign-On (SSO) with Okta and SAML 2.0 in our tenant?",
     "To configure Okta SSO: Navigate to Admin Portal > Security > SSO. Enter your Okta Metadata URL, Entity ID, and download the ACS certificate. Map email and role attributes accordingly.",
     "customer_support"),
    ("Extract the customer billing address, total amount, and invoice ID from this receipt text.",
     "Extracted JSON: {'invoice_id': 'INV-9821', 'total_amount_usd': 420.50, 'billing_address': '100 Market St, San Francisco, CA'}",
     "data_extraction"),
    ("What are the rate limits and token quotas for the Tier 3 REST API endpoints?",
     "Tier 3 REST API limits allow up to 1,200 requests per minute with a concurrent execution cap of 50 simultaneous workers.",
     "customer_support"),
    ("Generate a SQL query to calculate 30-day customer retention cohort analysis in DuckDB.",
     "SELECT cohort_month, COUNT(DISTINCT user_id) as total_users, SUM(CASE WHEN active_day_30 THEN 1 ELSE 0 END) as retained_day_30 FROM user_events GROUP BY 1 ORDER BY 1 DESC;",
     "code_generation"),
    ("Summarize the quarterly incident response report into 3 bullet points for executive review.",
     "• Total MTTR reduced by 42% via automated runbooks.\n• Zero Sev-1 outages breached SLA in Q3.\n• All post-mortem action items closed within 14 business days.",
     "summarization"),
    ("Classify the following customer ticket sentiment: 'The dashboard is extremely slow today and throwing 504 errors.'",
     "Classification: Negative (Category: Performance / Outage Incident, Priority: High)",
     "customer_support"),
    ("Provide python code to compute cosine similarity between two numpy vectors with L2 normalization.",
     "def cosine_sim(a, b):\n    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))",
     "code_generation")
]

FEATURES = ["customer_support", "code_generation", "data_extraction", "summarization", "semantic_search"]


def seed_database(force_reseed: bool = False) -> None:
    """Seed DuckDB with default tenants, initial semantic cache entries, and 3,000 telemetry rows."""
    conn = get_db_connection()

    # Check if already seeded
    existing_count = conn.execute("SELECT COUNT(*) FROM finops_telemetry_logs").fetchone()[0]
    if existing_count >= 1000 and not force_reseed:
        return

    # 1. Upsert Tenants
    for t in TENANTS_SEED_DATA:
        FinOpsDB.upsert_tenant(
            tenant_id=t["tenant_id"],
            tenant_name=t["tenant_name"],
            plan_tier=t["plan_tier"],
            monthly_mrr_usd=t["monthly_mrr_usd"],
            monthly_token_budget_usd=t["monthly_token_budget_usd"],
            current_month_spend_usd=0.0,
            enforce_hard_cap=t["enforce_hard_cap"]
        )

    # 2. Pre-seed Semantic Cache with verified prompt-response pairs
    for tenant in TENANTS_SEED_DATA:
        t_id = tenant["tenant_id"]
        for prompt_text, response_text, feature in FREQUENT_PROMPTS:
            semantic_cache_service.store(
                tenant_id=t_id,
                prompt_text=prompt_text,
                response_text=response_text,
                routed_model="cache-verified",
                prompt_tokens=len(prompt_text.split()) * 2,
                completion_tokens=len(response_text.split()) * 2
            )

    # 3. Generate 3,000 realistic historical requests across past 30 days
    now = datetime.now(timezone.utc)
    random.seed(42)
    np.random.seed(42)

    total_spend_by_tenant = {t["tenant_id"]: 0.0 for t in TENANTS_SEED_DATA}

    for i in range(3000):
        # Weighted tenant distribution (Acme & Globex generate more volume)
        t_choice = random.choices(
            TENANTS_SEED_DATA,
            weights=[40, 30, 15, 10, 5],
            k=1
        )[0]
        tenant_id = t_choice["tenant_id"]

        # Timestamp evenly distributed over 30 days with diurnal seasonality
        days_ago = random.uniform(0.05, 30.0)
        req_time = now - timedelta(days=days_ago)

        feature = random.choice(FEATURES)

        # 38% probability of cache hit for recurring semantic queries
        is_cache_hit = (random.random() < 0.38)

        if is_cache_hit:
            routed_model = "cache-hit"
            complexity = round(random.uniform(0.1, 0.45), 3)
            prompt_tok = random.randint(30, 200)
            comp_tok = random.randint(50, 250)
            latency_ms = round(random.uniform(7.5, 18.2), 2)  # Sub-20ms verified
            cogs_usd = settings.DEFAULT_OVERHEAD_COGS_USD
            # Savings vs frontier
            savings_usd = round(
                (prompt_tok / 1000.0 * settings.FRONTIER_INPUT_COST_PER_1K) +
                (comp_tok / 1000.0 * settings.FRONTIER_OUTPUT_COST_PER_1K),
                6
            )
        else:
            complexity = round(random.betavariate(2.2, 2.5), 3)
            prompt_tok = random.randint(60, 800)
            comp_tok = random.randint(80, 600)

            if complexity < settings.COMPLEXITY_ROUTING_THRESHOLD:
                # Routed to Local Ollama
                routed_model = settings.LOCAL_MODEL_NAME
                latency_ms = round(random.uniform(110.0, 290.0), 2)
                cogs_usd = round(
                    (prompt_tok / 1000.0 * settings.LOCAL_INPUT_COST_PER_1K) +
                    (comp_tok / 1000.0 * settings.LOCAL_OUTPUT_COST_PER_1K) +
                    settings.DEFAULT_OVERHEAD_COGS_USD,
                    6
                )
                savings_usd = round(
                    ((prompt_tok / 1000.0 * settings.FRONTIER_INPUT_COST_PER_1K) +
                     (comp_tok / 1000.0 * settings.FRONTIER_OUTPUT_COST_PER_1K)) - cogs_usd,
                    6
                )
            else:
                # Routed to Frontier Model
                routed_model = settings.FRONTIER_MODEL_NAME
                latency_ms = round(random.uniform(850.0, 2100.0), 2)
                cogs_usd = round(
                    (prompt_tok / 1000.0 * settings.FRONTIER_INPUT_COST_PER_1K) +
                    (comp_tok / 1000.0 * settings.FRONTIER_OUTPUT_COST_PER_1K) +
                    settings.DEFAULT_OVERHEAD_COGS_USD,
                    6
                )
                savings_usd = 0.0

        FinOpsDB.log_telemetry(
            req_id=f"req-{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            feature_tag=feature,
            routed_model=routed_model,
            complexity_score=complexity,
            cache_hit=is_cache_hit,
            prompt_tokens=prompt_tok,
            completion_tokens=comp_tok,
            cogs_usd=cogs_usd,
            savings_usd=savings_usd,
            latency_ms=latency_ms,
            created_at=req_time
        )
        total_spend_by_tenant[tenant_id] += cogs_usd

    # Update tenant spend tallies in DB
    for t_id, spend in total_spend_by_tenant.items():
        conn.execute("UPDATE tenants SET current_month_spend_usd = ? WHERE tenant_id = ?", [round(spend, 4), t_id])


if __name__ == "__main__":
    seed_database(force_reseed=True)
    print("Seeded 3,000 multi-tenant FinOps telemetry logs and semantic cache entries successfully.")
