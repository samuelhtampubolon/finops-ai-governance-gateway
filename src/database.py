"""DuckDB ledger database manager for FinOps telemetry, multi-tenant state, and semantic cache."""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import duckdb
import numpy as np

from src.config import settings

_db_lock = threading.Lock()
_connection: Optional[duckdb.DuckDBPyConnection] = None


def get_db_connection() -> duckdb.DuckDBPyConnection:
    """Provide a thread-safe connection to the DuckDB FinOps repository."""
    global _connection
    with _db_lock:
        if _connection is None:
            db_path = settings.resolved_db_path
            # Open persistent DuckDB database
            _connection = duckdb.connect(database=db_path, read_only=False)
            _init_tables(_connection)
        return _connection


def _init_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Initialize relational and time-series schemas if not already created."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id VARCHAR PRIMARY KEY,
            tenant_name VARCHAR NOT NULL,
            plan_tier VARCHAR NOT NULL,
            monthly_mrr_usd DOUBLE NOT NULL,
            monthly_token_budget_usd DOUBLE NOT NULL,
            current_month_spend_usd DOUBLE DEFAULT 0.0,
            enforce_hard_cap BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS semantic_cache_entries (
            cache_id VARCHAR PRIMARY KEY,
            tenant_id VARCHAR NOT NULL,
            prompt_norm VARCHAR NOT NULL,
            embedding BLOB NOT NULL,
            response_text VARCHAR NOT NULL,
            hit_count BIGINT DEFAULT 0,
            routed_model VARCHAR NOT NULL,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS finops_telemetry_logs (
            req_id VARCHAR PRIMARY KEY,
            tenant_id VARCHAR NOT NULL,
            feature_tag VARCHAR NOT NULL,
            routed_model VARCHAR NOT NULL,
            complexity_score DOUBLE NOT NULL,
            cache_hit BOOLEAN NOT NULL,
            prompt_tokens INTEGER NOT NULL,
            completion_tokens INTEGER NOT NULL,
            cogs_usd DOUBLE NOT NULL,
            savings_usd DOUBLE NOT NULL,
            latency_ms DOUBLE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Indexes for fast lookup and tenant isolation
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cache_tenant ON semantic_cache_entries (tenant_id);
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_telemetry_tenant ON finops_telemetry_logs (tenant_id, created_at);
    """)


class FinOpsDB:
    """Data Access Object providing atomic queries and transactions for the FinOps gateway."""

    @staticmethod
    def get_tenant(tenant_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection()
        with _db_lock:
            res = conn.execute(
                "SELECT tenant_id, tenant_name, plan_tier, monthly_mrr_usd, "
                "monthly_token_budget_usd, current_month_spend_usd, enforce_hard_cap "
                "FROM tenants WHERE tenant_id = ?",
                [tenant_id]
            ).fetchone()
            if not res:
                return None
            return {
                "tenant_id": res[0],
                "tenant_name": res[1],
                "plan_tier": res[2],
                "monthly_mrr_usd": float(res[3]),
                "monthly_token_budget_usd": float(res[4]),
                "current_month_spend_usd": float(res[5]),
                "enforce_hard_cap": bool(res[6])
            }

    @staticmethod
    def upsert_tenant(
        tenant_id: str,
        tenant_name: str,
        plan_tier: str,
        monthly_mrr_usd: float,
        monthly_token_budget_usd: float,
        current_month_spend_usd: float = 0.0,
        enforce_hard_cap: bool = True
    ) -> None:
        conn = get_db_connection()
        with _db_lock:
            conn.execute("""
                INSERT OR REPLACE INTO tenants (
                    tenant_id, tenant_name, plan_tier, monthly_mrr_usd,
                    monthly_token_budget_usd, current_month_spend_usd, enforce_hard_cap, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, [tenant_id, tenant_name, plan_tier, monthly_mrr_usd, monthly_token_budget_usd, current_month_spend_usd, enforce_hard_cap])

    @staticmethod
    def increment_tenant_spend(tenant_id: str, cost_usd: float) -> float:
        """Atomically increment the tenant's current monthly spend."""
        conn = get_db_connection()
        with _db_lock:
            conn.execute(
                "UPDATE tenants SET current_month_spend_usd = current_month_spend_usd + ? WHERE tenant_id = ?",
                [cost_usd, tenant_id]
            )
            res = conn.execute(
                "SELECT current_month_spend_usd FROM tenants WHERE tenant_id = ?",
                [tenant_id]
            ).fetchone()
            return float(res[0]) if res else 0.0

    @staticmethod
    def log_telemetry(
        req_id: str,
        tenant_id: str,
        feature_tag: str,
        routed_model: str,
        complexity_score: float,
        cache_hit: bool,
        prompt_tokens: int,
        completion_tokens: int,
        cogs_usd: float,
        savings_usd: float,
        latency_ms: float,
        created_at: Optional[datetime] = None
    ) -> None:
        conn = get_db_connection()
        with _db_lock:
            if created_at is None:
                conn.execute("""
                    INSERT INTO finops_telemetry_logs (
                        req_id, tenant_id, feature_tag, routed_model, complexity_score,
                        cache_hit, prompt_tokens, completion_tokens, cogs_usd, savings_usd,
                        latency_ms, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, [
                    req_id, tenant_id, feature_tag, routed_model, complexity_score,
                    cache_hit, prompt_tokens, completion_tokens, cogs_usd, savings_usd, latency_ms
                ])
            else:
                conn.execute("""
                    INSERT INTO finops_telemetry_logs (
                        req_id, tenant_id, feature_tag, routed_model, complexity_score,
                        cache_hit, prompt_tokens, completion_tokens, cogs_usd, savings_usd,
                        latency_ms, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    req_id, tenant_id, feature_tag, routed_model, complexity_score,
                    cache_hit, prompt_tokens, completion_tokens, cogs_usd, savings_usd, latency_ms, created_at
                ])

    @staticmethod
    def get_tenant_cache_entries(tenant_id: str) -> List[Dict[str, Any]]:
        """Retrieve all semantic cache entries for a specific tenant to ensure strict cache isolation."""
        conn = get_db_connection()
        with _db_lock:
            rows = conn.execute("""
                SELECT cache_id, tenant_id, prompt_norm, embedding, response_text,
                       hit_count, routed_model, prompt_tokens, completion_tokens
                FROM semantic_cache_entries
                WHERE tenant_id = ?
            """, [tenant_id]).fetchall()

            entries = []
            for r in rows:
                raw_blob = r[3]
                embedding_arr = np.frombuffer(raw_blob, dtype=np.float32)
                entries.append({
                    "cache_id": r[0],
                    "tenant_id": r[1],
                    "prompt_norm": r[2],
                    "embedding": embedding_arr,
                    "response_text": r[4],
                    "hit_count": r[5],
                    "routed_model": r[6],
                    "prompt_tokens": r[7],
                    "completion_tokens": r[8]
                })
            return entries

    @staticmethod
    def insert_cache_entry(
        cache_id: str,
        tenant_id: str,
        prompt_norm: str,
        embedding: np.ndarray,
        response_text: str,
        routed_model: str,
        prompt_tokens: int,
        completion_tokens: int
    ) -> None:
        conn = get_db_connection()
        embedding_blob = embedding.astype(np.float32).tobytes()
        with _db_lock:
            conn.execute("""
                INSERT OR REPLACE INTO semantic_cache_entries (
                    cache_id, tenant_id, prompt_norm, embedding, response_text,
                    hit_count, routed_model, prompt_tokens, completion_tokens,
                    created_at, last_accessed_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, [
                cache_id, tenant_id, prompt_norm, embedding_blob,
                response_text, routed_model, prompt_tokens, completion_tokens
            ])

    @staticmethod
    def record_cache_hit(cache_id: str) -> None:
        conn = get_db_connection()
        with _db_lock:
            conn.execute("""
                UPDATE semantic_cache_entries
                SET hit_count = hit_count + 1,
                    last_accessed_at = CURRENT_TIMESTAMP
                WHERE cache_id = ?
            """, [cache_id])

    @staticmethod
    def get_unit_economics_for_tenant(tenant_id: str) -> Dict[str, Any]:
        """Aggregate real-time gross margin, token spend, and cache savings for a tenant."""
        tenant = FinOpsDB.get_tenant(tenant_id)
        if not tenant:
            return {}

        conn = get_db_connection()
        with _db_lock:
            stats = conn.execute("""
                SELECT
                    COUNT(*) as total_requests,
                    COALESCE(SUM(cogs_usd), 0.0) as total_cogs_usd,
                    COALESCE(SUM(savings_usd), 0.0) as total_savings_usd,
                    COALESCE(SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END), 0) as cache_hits,
                    COALESCE(AVG(latency_ms), 0.0) as avg_latency_ms,
                    COALESCE(SUM(prompt_tokens + completion_tokens), 0) as total_tokens
                FROM finops_telemetry_logs
                WHERE tenant_id = ?
            """, [tenant_id]).fetchone()

        total_requests = stats[0]
        total_cogs = float(stats[1])
        total_savings = float(stats[2])
        cache_hits = stats[3]
        avg_latency = float(stats[4])
        total_tokens = stats[5]

        mrr = tenant["monthly_mrr_usd"]
        gross_margin_pct = ((mrr - total_cogs) / mrr * 100.0) if mrr > 0 else 0.0
        cache_hit_rate_pct = (cache_hits / total_requests * 100.0) if total_requests > 0 else 0.0

        return {
            "tenant_id": tenant_id,
            "tenant_name": tenant["tenant_name"],
            "plan_tier": tenant["plan_tier"],
            "monthly_mrr_usd": mrr,
            "monthly_token_budget_usd": tenant["monthly_token_budget_usd"],
            "current_month_spend_usd": tenant["current_month_spend_usd"],
            "total_requests": total_requests,
            "total_cogs_usd": round(total_cogs, 4),
            "total_savings_usd": round(total_savings, 4),
            "gross_margin_pct": round(gross_margin_pct, 2),
            "cache_hit_rate_pct": round(cache_hit_rate_pct, 2),
            "avg_latency_ms": round(avg_latency, 2),
            "total_tokens": total_tokens,
            "enforce_hard_cap": tenant["enforce_hard_cap"]
        }

    @staticmethod
    def get_all_tenants() -> List[Dict[str, Any]]:
        conn = get_db_connection()
        with _db_lock:
            rows = conn.execute("""
                SELECT tenant_id, tenant_name, plan_tier, monthly_mrr_usd,
                       monthly_token_budget_usd, current_month_spend_usd, enforce_hard_cap
                FROM tenants ORDER BY monthly_mrr_usd DESC
            """).fetchall()

            return [
                {
                    "tenant_id": r[0],
                    "tenant_name": r[1],
                    "plan_tier": r[2],
                    "monthly_mrr_usd": float(r[3]),
                    "monthly_token_budget_usd": float(r[4]),
                    "current_month_spend_usd": float(r[5]),
                    "enforce_hard_cap": bool(r[6])
                }
                for r in rows
            ]
