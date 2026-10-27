"""Security controls: OWASP LLM prompt injection firewall, PII scrubber, and JWT authentication."""

import base64
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple
import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.config import settings

bearer_scheme = HTTPBearer(auto_error=False)


# ==============================================================================
# 1. PII REDACTION & DATA SANITIZATION (OWASP LLM06)
# ==============================================================================

EMAIL_REGEX = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
)
PHONE_REGEX = re.compile(
    r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
)
SSN_REGEX = re.compile(
    r"\b\d{3}-\d{2}-\d{4}\b"
)
NIK_REGEX = re.compile(
    r"\b\d{16}\b"
)
API_KEY_REGEX = re.compile(
    r"\b(?:sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36}|bearer\s+[a-zA-Z0-9\-_.]{24,})\b",
    re.IGNORECASE
)
CREDIT_CARD_CANDIDATE_REGEX = re.compile(
    r"\b(?:\d{4}[-\s]?){3}\d{4}\b|\b\d{13,19}\b"
)


def luhn_checksum_valid(card_number_str: str) -> bool:
    """Validate credit card number using Luhn algorithm."""
    digits = [int(c) for c in card_number_str if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for i, digit in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = digit * 2
            checksum += doubled - 9 if doubled > 9 else doubled
        else:
            checksum += digit
    return checksum % 10 == 0


def redact_pii(text: str) -> Tuple[str, List[str]]:
    """Sanitize text by replacing sensitive PII with redacting placeholders.

    Returns:
        Tuple of (sanitized_text, list_of_redacted_categories)
    """
    if not text:
        return text, []

    redacted_categories: List[str] = []
    sanitized = text

    # 1. Email Redaction
    if EMAIL_REGEX.search(sanitized):
        sanitized = EMAIL_REGEX.sub("[REDACTED_EMAIL]", sanitized)
        redacted_categories.append("EMAIL")

    # 2. Credit Card Redaction (with Luhn validation to avoid false positives)
    def cc_replacer(match: re.Match) -> str:
        matched_str = match.group(0)
        clean_num = re.sub(r"[-\s]", "", matched_str)
        if luhn_checksum_valid(clean_num):
            redacted_categories.append("CREDIT_CARD")
            return "[REDACTED_CREDIT_CARD]"
        return matched_str

    sanitized = CREDIT_CARD_CANDIDATE_REGEX.sub(cc_replacer, sanitized)

    # 3. US SSN Redaction
    if SSN_REGEX.search(sanitized):
        sanitized = SSN_REGEX.sub("[REDACTED_NATIONAL_ID]", sanitized)
        redacted_categories.append("SSN")

    # 4. National ID / NIK 16-digit
    if NIK_REGEX.search(sanitized):
        sanitized = NIK_REGEX.sub("[REDACTED_NATIONAL_ID]", sanitized)
        redacted_categories.append("NIK")

    # 5. Bearer / API Key Redaction
    if API_KEY_REGEX.search(sanitized):
        sanitized = API_KEY_REGEX.sub("[REDACTED_API_KEY]", sanitized)
        redacted_categories.append("API_KEY")

    # 6. Phone Number Redaction
    if PHONE_REGEX.search(sanitized):
        sanitized = PHONE_REGEX.sub("[REDACTED_PHONE]", sanitized)
        redacted_categories.append("PHONE")

    return sanitized, list(set(redacted_categories))


# ==============================================================================
# 2. PROMPT INJECTION & JAILBREAK FIREWALL (OWASP LLM01)
# ==============================================================================

PROMPT_INJECTION_PATTERNS = [
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.IGNORECASE), 0.95, "Instruction override"),
    (re.compile(r"disregard\s+(all\s+)?(prior|previous)\s+(guidelines|instructions?|rules?)", re.IGNORECASE), 0.95, "Instruction override"),
    (re.compile(r"system\s+prompt\s+override", re.IGNORECASE), 0.98, "System prompt override attempt"),
    (re.compile(r"reveal\s+(the\s+)?(secret|internal|system|master)\s+(key|token|prompt|instructions?)", re.IGNORECASE), 0.90, "Secret/System prompt extraction"),
    (re.compile(r"what\s+is\s+your\s+(original|internal|hidden)\s+system\s+prompt", re.IGNORECASE), 0.85, "System prompt extraction"),
    (re.compile(r"you\s+are\s+now\s+(dan|unrestricted|god\s+mode|jailbroken)", re.IGNORECASE), 0.95, "Persona hijacking / DAN jailbreak"),
    (re.compile(r"bypass\s+(all\s+)?(content\s+filters?|safety\s+guidelines?|guardrails?)", re.IGNORECASE), 0.92, "Filter bypass attempt"),
    (re.compile(r"<\|im_start\|>system", re.IGNORECASE), 0.99, "Delimiter hijacking"),
    (re.compile(r"---END\s+OF\s+SYSTEM\s+PROMPT---", re.IGNORECASE), 0.99, "Delimiter hijacking"),
    (re.compile(r"print\s+the\s+preceding\s+text", re.IGNORECASE), 0.80, "Prompt leakage attempt"),
]


def inspect_prompt_injection(prompt: str) -> Tuple[bool, float, str]:
    """Inspect incoming prompt for malicious prompt injection or jailbreak patterns.

    Returns:
        Tuple of (is_flagged: bool, injection_score: float, detected_reason: str)
    """
    if not prompt:
        return False, 0.0, "Empty prompt"

    highest_score = 0.0
    detected_reasons: List[str] = []

    # Check known regex signatures
    for pattern, score, reason in PROMPT_INJECTION_PATTERNS:
        if pattern.search(prompt):
            if score > highest_score:
                highest_score = score
            detected_reasons.append(reason)

    # Check for base64 encoded payload smuggling
    base64_candidates = re.findall(r"[A-Za-z0-9+/]{24,}={0,2}", prompt)
    for candidate in base64_candidates:
        try:
            decoded = base64.b64decode(candidate).decode("utf-8", errors="ignore").lower()
            for pattern, score, reason in PROMPT_INJECTION_PATTERNS:
                if pattern.search(decoded):
                    highest_score = max(highest_score, score + 0.05)
                    detected_reasons.append(f"Obfuscated Base64 Injection ({reason})")
        except Exception:
            pass

    # Threshold for blocking
    is_blocked = highest_score >= 0.70
    reason_str = "; ".join(detected_reasons) if detected_reasons else "Clean prompt"
    return is_blocked, highest_score, reason_str


# ==============================================================================
# 3. JWT TOKEN AUTHENTICATION & MULTI-TENANCY ISOLATION
# ==============================================================================

def create_tenant_jwt(tenant_id: str, plan_tier: str = "enterprise", expires_delta: timedelta = None) -> str:
    """Issue a tenant-scoped JWT access token."""
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
    )
    payload = {
        "sub": tenant_id,
        "tenant_id": tenant_id,
        "plan_tier": plan_tier,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "iss": "finops-ai-governance-gateway"
    }
    encoded_jwt = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt


def decode_tenant_jwt(token: str) -> Dict[str, Any]:
    """Verify and decode a JWT bearer token."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer="finops-ai-governance-gateway"
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired. Please refresh your credentials."
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token signature or malformed payload."
        )


async def get_current_tenant(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme)
) -> Dict[str, Any]:
    """Dependency that extracts and validates the tenant context from HTTP Authorization Header."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization Bearer token header."
        )
    return decode_tenant_jwt(credentials.credentials)
