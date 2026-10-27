"""Query complexity scoring and hybrid model router (local Ollama vs frontier APIs)."""

import re
from typing import List, Tuple
from src.config import settings


# Keywords indicative of multi-step analytical reasoning and synthesis
REASONING_KEYWORDS = [
    "step by step", "chain of thought", "analyze", "synthesize", "architect",
    "tradeoff", "trade-off", "comparative analysis", "derivation", "mathematical proof",
    "counterfactual", "deductive reasoning", "edge cases", "formal verification",
    "algorithmic complexity", "causal inference", "root cause analysis", "multi-objective"
]

# Keywords indicative of complex code/schema generation
CODING_SYNTAX_KEYWORDS = [
    "refactor", "abstract syntax tree", "ast", "regex", "regular expression",
    "deadlock", "race condition", "memory leak", "assembly", "compiler",
    "recursive descent", "distributed transaction", "two-phase commit", "raft consensus",
    "microservices architecture", "sql query optimization", "dynamic programming"
]

# Keywords indicative of routine, low-complexity tasks
ROUTINE_TASK_KEYWORDS = [
    "classify", "categorize", "extract", "sentiment", "summarize in one sentence",
    "fix typo", "translate", "list 3", "what is the capital", "yes or no",
    "true or false", "rephrase", "format as bullet points", "synonym"
]


class ComplexityRouterService:
    """Evaluates prompt cognitive load and routes to most cost-effective model."""

    def __init__(self, threshold: float = settings.COMPLEXITY_ROUTING_THRESHOLD):
        self.threshold = threshold

    def evaluate(self, prompt: str, system_prompt: str = "") -> Tuple[float, str, List[str]]:
        """Score query complexity from 0.0 (simple/routine) to 1.0 (frontier reasoning).

        Returns:
            Tuple of (complexity_score, routed_model_name, list_of_decision_factors)
        """
        combined = f"{system_prompt} {prompt}".lower()
        tokens = combined.split()
        token_count = len(tokens)
        decision_factors: List[str] = []

        # 1. Base Token Length Score (up to 0.35)
        # 0-50 tokens: 0.05
        # 50-200 tokens: 0.15
        # 200-600 tokens: 0.25
        # >600 tokens: 0.35
        if token_count > 600:
            length_score = 0.35
            decision_factors.append(f"High token length ({token_count} words)")
        elif token_count > 200:
            length_score = 0.25
            decision_factors.append(f"Moderate-high token length ({token_count} words)")
        elif token_count > 50:
            length_score = 0.15
        else:
            length_score = 0.05

        # 2. Reasoning Keywords Score (up to 0.40)
        reasoning_hits = [kw for kw in REASONING_KEYWORDS if kw in combined]
        reasoning_score = min(0.40, len(reasoning_hits) * 0.15)
        if reasoning_hits:
            decision_factors.append(f"Reasoning keywords detected: {', '.join(reasoning_hits[:3])}")

        # 3. Code & Structural Complexity Score (up to 0.35)
        coding_hits = [kw for kw in CODING_SYNTAX_KEYWORDS if kw in combined]
        coding_score = min(0.35, len(coding_hits) * 0.15)
        if coding_hits:
            decision_factors.append(f"Technical/code complexity detected: {', '.join(coding_hits[:3])}")

        # 4. Routine Task Deflection Penalty (reduces score by up to 0.25)
        routine_hits = [kw for kw in ROUTINE_TASK_KEYWORDS if kw in combined]
        routine_penalty = min(0.25, len(routine_hits) * 0.10)
        if routine_hits and not (reasoning_hits or coding_hits):
            decision_factors.append(f"Routine classification/extraction pattern: {', '.join(routine_hits[:2])}")

        # Composite score clamped in [0.0, 1.0]
        raw_score = length_score + reasoning_score + coding_score - routine_penalty
        final_score = max(0.05, min(1.0, round(raw_score, 3)))

        # Routing decision
        if final_score >= self.threshold:
            routed_model = settings.FRONTIER_MODEL_NAME
            decision_factors.append(f"Score {final_score:.2f} >= threshold {self.threshold:.2f} -> Frontier Model")
        else:
            routed_model = settings.LOCAL_MODEL_NAME
            decision_factors.append(f"Score {final_score:.2f} < threshold {self.threshold:.2f} -> Local Ollama Model")

        return final_score, routed_model, decision_factors


complexity_router_service = ComplexityRouterService()
