"""Shared keyword-overlap heuristic for mapping free text onto a taxonomy
category id - used both when labelling KB documents at build time
(kb/build_pipeline.py) and when categorising an incident at runtime
(orchestration/pipeline.py), so the two stay consistent with each other.
"""
from __future__ import annotations

import re


def match_error_category(text: str, taxonomy: dict) -> str | None:
    text_lower = text.lower()
    best_score = 0
    best_id: str | None = None
    for c in taxonomy.get("categories", []):
        keywords = re.findall(r"[a-z]+", (c["label"] + " " + c["description"]).lower())
        score = sum(text_lower.count(kw) for kw in keywords if len(kw) > 3)
        if score > best_score:
            best_score = score
            best_id = c["id"]
    return best_id
