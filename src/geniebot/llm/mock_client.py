"""Deterministic local/dev/test LLM client. No network calls. Produces
schema-valid, evidence-grounded responses by actually reading the prompt
content (not hardcoded canned text) so it exercises the same downstream
validation, grounding-check and confidence-gate logic a real model response
would - the point is a fully runnable pipeline without platform access, not
a stub that always returns the same thing.
"""
from __future__ import annotations

import json
import re
import time

from geniebot.llm.client import EmbeddingResponse, LLMClient, LLMResponse
from geniebot.llm.local_embeddings import deterministic_embedding

_ERROR_LINE = re.compile(
    r"^\s*(?P<lineno>\d+):\s*(?P<text>.*\b(?P<exc>[A-Za-z_][A-Za-z0-9_.]*"
    r"(?:Error|Exception|Timeout|Fault|Failure))\b.*)$",
    re.MULTILINE,
)


def _extract_json_after(text: str, marker: str) -> dict | None:
    """Finds `marker` in text, then bracket-matches the next {...} block."""
    idx = text.find(marker)
    if idx == -1:
        return None
    start = text.find("{", idx)
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _extract_json_array_after(text: str, marker: str) -> list | None:
    idx = text.find(marker)
    if idx == -1:
        return None
    start = text.find("[", idx)
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _mock_parse(user: str) -> dict:
    matches = list(_ERROR_LINE.finditer(user))
    if not matches:
        return {
            "exception_type": "",
            "exception_message": "",
            "failing_module": "",
            "root_frame": "",
            "cascading_errors": [],
            "evidence_lines": [],
            "parse_status": "unparseable",
        }

    root = matches[0]
    cascading = [m.group("exc") for m in matches[1:]]
    module_match = re.search(r"in (?:module )?([\w./]+\.\w+)", root.group("text"))
    return {
        "exception_type": root.group("exc"),
        "exception_message": root.group("text").strip()[:500],
        "failing_module": module_match.group(1) if module_match else "unknown",
        "root_frame": root.group("text").strip()[:300],
        "cascading_errors": cascading,
        "evidence_lines": [
            {"line_no": int(m.group("lineno")), "text": m.group("text").strip()} for m in matches
        ],
        "parse_status": "parsed",
    }


def _mock_diagnose(user: str) -> dict:
    parse_output = _extract_json_after(user, "Parsed error signature:") or {}
    kb_context = user.split("Retrieved knowledge base context")[-1]

    evidence_lines = parse_output.get("evidence_lines", [])
    citations = []
    if evidence_lines:
        citations.append({"type": "log", "ref": str(evidence_lines[0]["line_no"])})

    kb_refs = re.findall(r'"source_ref":\s*"([^"]+)"', kb_context)
    if kb_refs:
        citations.append({"type": "kb", "ref": kb_refs[0]})
    has_precedent = bool(kb_refs)

    parsed_ok = parse_output.get("parse_status") == "parsed"
    if not parsed_ok:
        return {
            "root_cause": "",
            "proposed_resolution": "",
            "resolution_type": "escalate",
            "rerun_parameters": {},
            "citations": [],
            "confidence": 0.0,
            "insufficient_information": True,
        }

    exc_type = parse_output.get("exception_type", "UnknownError")
    module = parse_output.get("failing_module", "unknown module")

    if has_precedent:
        return {
            "root_cause": f"{exc_type} raised in {module}, consistent with a previously resolved incident.",
            "proposed_resolution": (
                f"Apply the precedent resolution for {exc_type} in {module}; "
                "recommend a controlled rerun once the underlying condition is cleared."
            ),
            "resolution_type": "controlled_rerun",
            "rerun_parameters": {"retry_module": module, "max_attempts": 1},
            "citations": citations,
            "confidence": 0.82,
            "insufficient_information": False,
        }
    return {
        "root_cause": f"{exc_type} raised in {module}; no matching precedent found in the knowledge base.",
        "proposed_resolution": "Escalate to L2 for manual investigation - no precedent to base an automated recommendation on.",
        "resolution_type": "escalate",
        "rerun_parameters": {},
        "citations": citations,
        "confidence": 0.3,
        "insufficient_information": True,
    }


def _mock_template(user: str) -> dict:
    parse_output = _extract_json_after(user, "Parsed error signature:") or {}
    diagnosis = _extract_json_after(user, "Diagnosis:") or {}
    meta_section = user.split("Parsed error signature:")[0]
    meta = dict(re.findall(r"(\w+)=(\S+)", meta_section))

    unknown_fields: list[str] = []
    priority = None
    if not diagnosis.get("root_cause"):
        unknown_fields.append("priority")
    else:
        priority = "P3"

    evidence_refs = []
    for c in diagnosis.get("citations", []):
        evidence_refs.append({"type": c.get("type", "log"), "ref": c.get("ref", "")})

    return {
        "incident_id": meta.get("incident_id", ""),
        "bot_id": meta.get("bot_id", ""),
        "job_run_id": meta.get("job_run_id", ""),
        "environment": meta.get("environment", "production"),
        "error_category": parse_output.get("exception_type", "UNKNOWN") or "UNKNOWN",
        "summary": (diagnosis.get("root_cause") or "Unable to determine root cause from available evidence")[:500],
        "root_cause": diagnosis.get("root_cause", ""),
        "recommended_action": diagnosis.get("proposed_resolution", "Manual investigation required."),
        "evidence_refs": evidence_refs,
        "log_s3_uri": meta.get("log_s3_uri", ""),
        "priority": priority,
        "unknown_fields": unknown_fields,
    }


class MockLLMClient(LLMClient):
    async def chat_completion(
        self,
        *,
        system: str,
        user: str,
        model: str,
        temperature: float = 0.0,
        max_output_tokens: int = 1500,
    ) -> LLMResponse:
        start = time.perf_counter()
        if "Log Parser Agent" in system:
            payload = _mock_parse(user)
        elif "Diagnostic" in system and "Triage" in system:
            payload = _mock_diagnose(user)
        elif "Template Generator Agent" in system:
            payload = _mock_template(user)
        else:
            payload = {}

        content = json.dumps(payload)
        latency_ms = (time.perf_counter() - start) * 1000
        return LLMResponse(
            content=content,
            model=model,
            prompt_tokens=max(1, len(system) // 4 + len(user) // 4),
            completion_tokens=max(1, len(content) // 4),
            latency_ms=latency_ms,
        )

    async def embed(self, texts: list[str], *, model: str) -> EmbeddingResponse:
        vectors = [deterministic_embedding(t) for t in texts]
        return EmbeddingResponse(
            vectors=vectors, model=model, tokens=sum(max(1, len(t) // 4) for t in texts)
        )
