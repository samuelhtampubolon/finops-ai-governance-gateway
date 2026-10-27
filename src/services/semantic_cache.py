"""Zero-dependency local vector semantic cache engine with sub-20ms latency and multi-tenant isolation."""

import hashlib
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from src.config import settings
from src.database import FinOpsDB


class VectorEmbeddingEngine:
    """Deterministic, zero-dependency embedding generator based on word/character n-gram hashing.

    Maps text into a dense, normalized L2 vector space of fixed dimension (default 128)
    guaranteeing reproducibility offline without external API keys or heavy torch models.
    """

    def __init__(self, dimensions: int = settings.CACHE_VECTOR_DIMENSIONS):
        self.dimensions = dimensions

    def clean_text(self, text: str) -> str:
        """Normalize whitespace, lower-case, and strip punctuation."""
        lowered = text.lower().strip()
        lowered = re.sub(r"\bsso\b", "single sign on sso", lowered)
        lowered = re.sub(r"\bapi\b", "api interface", lowered)
        lowered = re.sub(r"\bllm\b", "large language model llm", lowered)
        cleaned = re.sub(r"[^\w\s]", " ", lowered)
        return re.sub(r"\s+", " ", cleaned).strip()

    def embed(self, text: str) -> np.ndarray:
        """Generate a unit-normalized vector embedding for the input text."""
        cleaned = self.clean_text(text)
        vector = np.zeros(self.dimensions, dtype=np.float32)

        if not cleaned:
            return vector

        tokens = cleaned.split()
        # Word unigrams and bigrams
        features = list(tokens)
        for i in range(len(tokens) - 1):
            features.append(f"{tokens[i]}_{tokens[i+1]}")

        # Character tri-grams for subword robustness
        for token in tokens:
            if len(token) >= 3:
                for j in range(len(token) - 2):
                    features.append(token[j:j+3])

        # Feature hashing into dimensional buckets with MD5 salt
        for feat in features:
            h = int(hashlib.md5(feat.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dimensions
            sign = 1.0 if ((h >> 8) % 2 == 0) else -1.0
            vector[idx] += sign

        # L2 unit normalization
        norm = np.linalg.norm(vector)
        if norm > 1e-8:
            vector = vector / norm
        return vector

    @staticmethod
    def cosine_similarity(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        """Compute cosine similarity between two normalized vectors."""
        norm_a = np.linalg.norm(vec_a)
        norm_b = np.linalg.norm(vec_b)
        if norm_a < 1e-8 or norm_b < 1e-8:
            return 0.0
        return float(np.dot(vec_a, vec_b) / (norm_a * norm_b))


class SemanticCacheService:
    """Semantic caching service with multi-tenant data isolation and ultra-fast lookup."""

    def __init__(self, similarity_threshold: float = settings.SEMANTIC_SIMILARITY_THRESHOLD):
        self.embedding_engine = VectorEmbeddingEngine()
        self.similarity_threshold = similarity_threshold
        # In-memory hot cache cache_id -> entry, partitioned by tenant_id
        self._hot_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._load_cache_from_db()

    def _load_cache_from_db(self) -> None:
        """Pre-warm in-memory cache partitions for each tenant from DuckDB."""
        try:
            tenants = FinOpsDB.get_all_tenants()
            for t in tenants:
                t_id = t["tenant_id"]
                entries = FinOpsDB.get_tenant_cache_entries(t_id)
                self._hot_cache[t_id] = entries
        except Exception:
            # Table might be empty on initial bootstrap
            pass

    def lookup(
        self, tenant_id: str, prompt_text: str
    ) -> Tuple[Optional[Dict[str, Any]], float, float]:
        """Lookup cached completion for a prompt within a tenant's private cache partition.

        Returns:
            Tuple of (matched_entry or None, similarity_score, lookup_latency_ms)
        """
        start_time = time.perf_counter()
        query_vec = self.embedding_engine.embed(prompt_text)

        # Retrieve entries strictly within tenant partition
        entries = self._hot_cache.get(tenant_id)
        if entries is None:
            entries = FinOpsDB.get_tenant_cache_entries(tenant_id)
            self._hot_cache[tenant_id] = entries

        best_match: Optional[Dict[str, Any]] = None
        highest_similarity = 0.0

        for entry in entries:
            sim = self.embedding_engine.cosine_similarity(query_vec, entry["embedding"])
            if sim > highest_similarity:
                highest_similarity = sim
                if sim >= self.similarity_threshold:
                    best_match = entry

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        if best_match is not None:
            # Increment hit counter in DuckDB
            FinOpsDB.record_cache_hit(best_match["cache_id"])
            best_match["hit_count"] = best_match.get("hit_count", 0) + 1

        return best_match, round(highest_similarity, 4), round(latency_ms, 2)

    def store(
        self,
        tenant_id: str,
        prompt_text: str,
        response_text: str,
        routed_model: str,
        prompt_tokens: int,
        completion_tokens: int
    ) -> str:
        """Store newly generated completion into tenant's isolated semantic cache."""
        cache_id = f"cache-{uuid.uuid4().hex[:12]}"
        prompt_norm = self.embedding_engine.clean_text(prompt_text)
        embedding = self.embedding_engine.embed(prompt_text)

        # Insert to DuckDB persistent store
        FinOpsDB.insert_cache_entry(
            cache_id=cache_id,
            tenant_id=tenant_id,
            prompt_norm=prompt_norm,
            embedding=embedding,
            response_text=response_text,
            routed_model=routed_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens
        )

        # Update in-memory hot cache partition
        if tenant_id not in self._hot_cache:
            self._hot_cache[tenant_id] = []

        new_entry = {
            "cache_id": cache_id,
            "tenant_id": tenant_id,
            "prompt_norm": prompt_norm,
            "embedding": embedding,
            "response_text": response_text,
            "hit_count": 0,
            "routed_model": routed_model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens
        }
        self._hot_cache[tenant_id].append(new_entry)
        return cache_id

    def clear_tenant_cache(self, tenant_id: str) -> None:
        """Flush cache partition for tenant."""
        self._hot_cache[tenant_id] = []


semantic_cache_service = SemanticCacheService()
