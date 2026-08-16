"""Semantic-boundary chunking - doc section 4.3:

- one chunk answers one question: what failed, why it failed, or how it
  was fixed
- keep the exception signature in the same chunk as its resolution
- retain a short parent-document summary in each chunk's metadata
- avoid chunking mid stack trace; truncate long traces to the distinctive
  frames rather than splitting them

Pure functions, no I/O, so chunking quality is unit-testable directly
against sample documents (tests/unit/test_chunking.py).
"""
from __future__ import annotations

import re
from typing import Literal

from geniebot.schemas.kb import KBChunkCandidate

_STACK_FRAME = re.compile(r"^\s*(at\s|File \"|Caused by:|\.\.\.\s*\d+\s*more)", re.MULTILINE)

_SECTION_HEADERS = {
    "problem": re.compile(r"(?im)^\s*(problem|symptom|error)s?\s*:\s*$"),
    "cause": re.compile(r"(?im)^\s*(root cause|diagnosis|why)\s*:\s*$"),
    "resolution": re.compile(r"(?im)^\s*(resolution|fix|steps?|recommended action)s?\s*:\s*$"),
}

MAX_TRACE_LINES = 8
KEEP_HEAD = 3
KEEP_TAIL = 3


def truncate_stack_trace(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _STACK_FRAME.match(lines[i]):
            block_start = i
            while i < len(lines) and (_STACK_FRAME.match(lines[i]) or lines[i].strip() == ""):
                i += 1
            block = [ln for ln in lines[block_start:i] if ln.strip()]
            if len(block) > MAX_TRACE_LINES:
                omitted = len(block) - KEEP_HEAD - KEEP_TAIL
                out.extend(block[:KEEP_HEAD])
                out.append(f"    ... ({omitted} frames omitted) ...")
                out.extend(block[-KEEP_TAIL:])
            else:
                out.extend(block)
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _split_sections(text: str) -> dict[str, str]:
    matches: list[tuple[str, int, int]] = []
    for name, pattern in _SECTION_HEADERS.items():
        for m in pattern.finditer(text):
            matches.append((name, m.start(), m.end()))
    if not matches:
        return {}
    matches.sort(key=lambda t: t[1])

    sections: dict[str, str] = {}
    for idx, (name, _start, end) in enumerate(matches):
        next_start = matches[idx + 1][1] if idx + 1 < len(matches) else len(text)
        content = text[end:next_start].strip()
        if content:
            sections[name] = (sections.get(name, "") + "\n\n" + content).strip()
    return sections


def summarise(text: str, max_chars: int = 240) -> str:
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if len(first_line) <= max_chars:
        return first_line
    return first_line[: max_chars - 1].rstrip() + "…"


def chunk_document(
    *,
    doc_id: str,
    doc_type: Literal["resolved_incident", "sop", "template", "runbook"],
    source_ref: str,
    error_category: str,
    classification: Literal["public", "internal", "confidential", "restricted"],
    raw_text: str,
    parent_summary: str | None = None,
) -> list[KBChunkCandidate]:
    text = truncate_stack_trace(raw_text.strip())
    if not text:
        return []

    summary = parent_summary or summarise(text)
    sections = _split_sections(text)

    chunks: list[str] = []
    if sections:
        # Keep "what failed" + "how it was fixed" together (doc 4.3), cause
        # gets its own chunk only when there's enough distinct content to
        # answer a different question than the fix.
        problem_and_fix = "\n\n".join(
            part for part in (sections.get("problem"), sections.get("resolution")) if part
        )
        if problem_and_fix:
            chunks.append(problem_and_fix)
        if sections.get("cause"):
            chunks.append(sections["cause"])
        if not chunks:
            chunks.append(text)
    else:
        chunks.append(text)

    return [
        KBChunkCandidate(
            doc_id=doc_id,
            doc_type=doc_type,
            content=chunk_text,
            error_category=error_category,
            source_ref=source_ref,
            parent_summary=summary,
            classification=classification,
        )
        for chunk_text in chunks
    ]
