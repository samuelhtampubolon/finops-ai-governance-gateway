# Architecture Decision Records (ADR)
## FinOps & Hybrid AI Resource Governance Gateway

---

### ADR-001: In-Process Deterministic Vector Semantic Cache with DuckDB Persistence

- **Status:** Accepted
- **Context:** Enterprise applications experience redundant inference costs (1,500ms+ latency and $0.015/1k tokens) when users issue semantically similar or duplicate queries. Relying on an external cloud embedding API and Redis vector store introduces network latency (50–120ms), runtime cost per embedding, and operational failure modes in offline or edge deployments.
- **Decision:** Implement an in-process, deterministic feature-hashing n-gram vector engine (128-dimensional unit normalized vectors) coupled with DuckDB for persistent BLOB storage and an in-memory hot index partitioned by tenant.
- **Consequences:**
  - **Pros:** Sub-20ms verified lookup latency (P95 < 15ms), zero network hops, zero external Redis or paid API key dependencies, deterministic offline reproducibility.
  - **Cons:** Vocabulary domain nuance for complex multilingual idioms is lower than multi-billion parameter transformer embeddings, mitigated via acronym expansion and cosine thresholding at 0.92.

---

### ADR-002: Deterministic Cognitive Complexity Routing (Local Ollama vs. Frontier APIs)

- **Status:** Accepted
- **Context:** Routing every query directly to frontier models (GPT-5 / Claude 3.5 Sonnet at $0.015/1k tokens) rapidly erodes software gross margins below the target 75% threshold, especially for routine classification, extraction, or single-turn customer service FAQs.
- **Decision:** Implement a multi-factor cognitive complexity classifier (scoring queries between 0.0 and 1.0) based on token length, multi-step analytical reasoning keywords, and syntactic/structural constraints. Prompts scoring below threshold (0.65) route to local Ollama models ($0.0001/1k tokens), while high-complexity prompts route to Frontier APIs.
- **Consequences:**
  - **Pros:** 150x cost reduction on routine workloads, predictable unit economics, prevents margin dilution.
  - **Cons:** Queries near the 0.65 threshold boundary require calibrated keyword weighting to prevent suboptimal routing.

---

### ADR-003: Strict Multi-Tenant Cache Isolation and Real-Time COGS Attribution

- **Status:** Accepted
- **Context:** In multi-tenant SaaS environments, sharing semantic cache entries across organizations risks leaking proprietary data or intellectual property (OWASP API1 & LLM06). Furthermore, platform engineering lacks visibility into tenant-specific unit economics.
- **Decision:** Strictly partition vector search and memory pools by `tenant_id`. Every request is authenticated via signed JWT bearer tokens and logged into an append-only DuckDB time-series ledger capturing prompt tokens, completion tokens, model tier, and exact COGS in USD.
- **Consequences:**
  - **Pros:** Mathematical impossibility of cross-tenant cache leakage; instant, audit-grade unit economics and margin calculations per tenant.
  - **Cons:** Redundant cache entries across distinct tenants for identical public queries, accepted as a necessary security trade-off.

---

### ADR-004: Ingress Edge Prompt Firewall (OWASP LLM01) and PII Redaction (OWASP LLM06)

- **Status:** Accepted
- **Context:** Frontier and local LLMs are vulnerable to indirect/direct prompt injection, persona hijacking (DAN), system prompt leakage, and PII harvesting.
- **Decision:** Enforce ingress inspection before semantic cache storage or model dispatch. Prompts are evaluated against known jailbreak signatures, base64 payload smuggling, and delimiter hijacking (blocking with HTTP 400). PII (emails, Luhn-verified credit cards, SSNs, NIKs, bearer tokens) is automatically scrubbed and replaced with redaction placeholders.
- **Consequences:**
  - **Pros:** Defense-in-depth security at the gateway perimeter; prevents sensitive data from ever reaching inference backends or cached vectors.
  - **Cons:** Minimal microsecond regex evaluation overhead (<1.5ms).

---

### ADR-005: Pre-Inference Budget Circuit Breaker and Denial-of-Wallet Mitigation (OWASP API4)

- **Status:** Accepted
- **Context:** Runaway automation, loops, or malicious traffic can exhaust SaaS gross margins by draining frontier API quotas (Denial of Wallet).
- **Decision:** Implement atomic spend tracking in DuckDB. Before dispatching expensive frontier calls, the gateway verifies `current_month_spend_usd + estimated_cost <= monthly_token_budget_usd`. If exhausted and hard cap is enabled, the gateway rejects with HTTP 429 Too Many Requests; if soft cap is enabled, it automatically downgrades to local low-cost models.
- **Consequences:**
  - **Pros:** Guaranteed gross margin preservation (protects 75%+ target margin), prevents catastrophic budget overruns.
  - **Cons:** Hard-capped tenants may experience request throttling until budget renewal or limit upgrade.
