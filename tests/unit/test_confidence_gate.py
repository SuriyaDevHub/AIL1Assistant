from geniebot.agents.confidence_gate import evaluate_confidence_gate
from geniebot.schemas.diagnosis import Citation, DiagnosisOutput

TAXONOMY = {
    "categories": [
        {"id": "CONNECTIVITY", "label": "Connectivity", "description": "network", "rollout_status": "pilot"},
        {"id": "DATA_VALIDATION", "label": "Data validation", "description": "bad data", "rollout_status": "disabled"},
    ]
}


def _diagnosis(**overrides) -> DiagnosisOutput:
    defaults = {
        "root_cause": "db down",
        "proposed_resolution": "retry",
        "resolution_type": "controlled_rerun",
        "citations": [Citation(type="log", ref="1")],
        "confidence": 0.9,
        "insufficient_information": False,
    }
    defaults.update(overrides)
    return DiagnosisOutput.model_validate(defaults)


def test_passes_when_all_conditions_met():
    result = evaluate_confidence_gate(
        _diagnosis(), error_category="CONNECTIVITY", taxonomy=TAXONOMY, auto_resolve_min=0.8
    )
    assert result.passed
    assert result.reasons == []


def test_fails_below_confidence_threshold():
    result = evaluate_confidence_gate(
        _diagnosis(confidence=0.5), error_category="CONNECTIVITY", taxonomy=TAXONOMY, auto_resolve_min=0.8
    )
    assert not result.passed
    assert any("confidence" in r for r in result.reasons)


def test_fails_without_citations():
    result = evaluate_confidence_gate(
        _diagnosis(citations=[]), error_category="CONNECTIVITY", taxonomy=TAXONOMY, auto_resolve_min=0.8
    )
    assert not result.passed
    assert any("citations" in r for r in result.reasons)


def test_fails_when_insufficient_information():
    result = evaluate_confidence_gate(
        _diagnosis(insufficient_information=True), error_category="CONNECTIVITY", taxonomy=TAXONOMY, auto_resolve_min=0.8
    )
    assert not result.passed


def test_fails_when_resolution_type_escalate():
    result = evaluate_confidence_gate(
        _diagnosis(resolution_type="escalate"), error_category="CONNECTIVITY", taxonomy=TAXONOMY, auto_resolve_min=0.8
    )
    assert not result.passed


def test_fails_when_category_not_rolled_out():
    result = evaluate_confidence_gate(
        _diagnosis(), error_category="DATA_VALIDATION", taxonomy=TAXONOMY, auto_resolve_min=0.8
    )
    assert not result.passed
    assert any("rollout_status" in r for r in result.reasons)


def test_fails_when_category_unknown():
    result = evaluate_confidence_gate(
        _diagnosis(), error_category=None, taxonomy=TAXONOMY, auto_resolve_min=0.8
    )
    assert not result.passed
