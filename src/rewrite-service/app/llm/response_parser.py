from __future__ import annotations

import re
from dataclasses import dataclass

_SQL_START_RE = re.compile(
    r"\bSELECT\b|\bWITH\b\s+(?:RECURSIVE\s+)?[A-Za-z_][\w$]*\s+AS\s*\(",
    re.IGNORECASE,
)
_UNSAFE_SQL_START_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|CALL|DO)\b",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"^\s*```(?:sql)?\s*|\s*```\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class RejectedModelFragment:
    text: str
    reason: str


@dataclass(frozen=True)
class ParsedModelResponse:
    candidates: list[str]
    rejected: list[RejectedModelFragment]


def extract_sql_candidates(response_text: str, max_candidates: int) -> list[str]:
    return parse_model_response(response_text, max_candidates).candidates


def parse_model_response(response_text: str, max_candidates: int) -> ParsedModelResponse:
    text = response_text.strip()
    if not text or text.upper() == "NO_OPTIMIZATION":
        return ParsedModelResponse(candidates=[], rejected=[])

    candidates = []
    rejected = []
    for part in re.split(r"^\s*---\s*$", text, flags=re.MULTILINE):
        candidate, rejection = _extract_one(part)
        if candidate:
            candidates.append(candidate)
        elif rejection:
            rejected.append(rejection)
        if len(candidates) >= max_candidates:
            break
    return ParsedModelResponse(candidates=candidates, rejected=rejected)


def _extract_one(text: str) -> tuple[str | None, RejectedModelFragment | None]:
    cleaned = _strip_fences(text.strip())
    if cleaned.upper() == "NO_OPTIMIZATION":
        return None, None

    start = _SQL_START_RE.search(cleaned)
    if start is None:
        reason = (
            "unsafe_non_select_output"
            if _UNSAFE_SQL_START_RE.search(cleaned)
            else "non_sql_output"
        )
        return None, RejectedModelFragment(text=cleaned, reason=reason)
    cleaned = cleaned[start.start() :].strip()

    semicolon = cleaned.find(";")
    if semicolon >= 0:
        remainder = cleaned[semicolon + 1 :].strip()
        if _SQL_START_RE.search(remainder) or _UNSAFE_SQL_START_RE.search(remainder):
            return None, RejectedModelFragment(text=cleaned, reason="multiple_statements")
        cleaned = cleaned[: semicolon + 1]

    return (cleaned, None) if cleaned else (None, None)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()
