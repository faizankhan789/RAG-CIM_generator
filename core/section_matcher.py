"""Deterministic matching of PDF heading text to the canonical CIM section taxonomy.

No LLM — this is a fixed lookup table plus light text normalization. Mirrors the
10-section structure core/llm.py and core/templates.py already treat as standard.
"""

from __future__ import annotations

import re

CANONICAL_SECTIONS: list[str] = [
    "I. Executive Summary",
    "II. Company Overview",
    "III. Financial Information",
    "IV. Operations",
    "V. Marketing and Sales",
    "VI. Legal and Regulatory",
    "VII. Human Resources",
    "VIII. Growth Opportunities",
    "IX. Risks",
    "X. Appendix",
]

_SYNONYMS: dict[str, str] = {
    "executive summary": "I. Executive Summary",
    "summary": "I. Executive Summary",
    "company overview": "II. Company Overview",
    "overview": "II. Company Overview",
    "about us": "II. Company Overview",
    "the business": "II. Company Overview",
    "financial information": "III. Financial Information",
    "financials": "III. Financial Information",
    "financial overview": "III. Financial Information",
    "the numbers": "III. Financial Information",
    "operations": "IV. Operations",
    "marketing and sales": "V. Marketing and Sales",
    "marketing & sales": "V. Marketing and Sales",
    "sales and marketing": "V. Marketing and Sales",
    "legal and regulatory": "VI. Legal and Regulatory",
    "legal": "VI. Legal and Regulatory",
    "human resources": "VII. Human Resources",
    "hr": "VII. Human Resources",
    "the team": "VII. Human Resources",
    "growth opportunities": "VIII. Growth Opportunities",
    "growth": "VIII. Growth Opportunities",
    "risks": "IX. Risks",
    "risk factors": "IX. Risks",
    "appendix": "X. Appendix",
}

# Every canonical heading also matches its own exact (normalized) text.
for _heading in CANONICAL_SECTIONS:
    _key = _heading.split(". ", 1)[1].lower()
    _SYNONYMS.setdefault(_key, _heading)


def _normalize(text: str) -> str:
    """Strip roman-numeral/number prefixes and punctuation, lowercase, collapse spaces."""
    text = re.sub(r"^[IVXLCDM]+\.\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"^\d+[\.\)]\s*", "", text)
    text = re.sub(r"[^a-z0-9 &]", "", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def match_sections(detected_headings: list[str]) -> dict[str, str]:
    """Map canonical CIM sections to the uploaded PDF's own heading label, where found.

    Returns a dict shaped like core/templates.py TEMPLATES["classic"]["headings"]:
    canonical heading -> display label taken verbatim from the PDF. Canonical
    sections with no match are simply absent from the result — callers (see
    core/pdf_style_extractor.py) fall back to the classic template's own wording
    for those, and surface a warning rather than failing silently.
    """
    result: dict[str, str] = {}
    for raw in detected_headings:
        canonical = _SYNONYMS.get(_normalize(raw))
        if canonical and canonical not in result:
            result[canonical] = raw.strip()
    return result
