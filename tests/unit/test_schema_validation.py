import pytest
from jsonschema import ValidationError
from pydantic import ValidationError as PydanticValidationError

from geniebot.schemas.diagnosis import DiagnosisOutput
from geniebot.schemas.parser import ParseOutput
from geniebot.schemas.template import TemplatePayload, validate_against_template_schema


def test_parse_output_accepts_minimal_valid_payload():
    out = ParseOutput.model_validate({"parse_status": "unparseable"})
    assert out.parse_status == "unparseable"
    assert out.evidence_lines == []


def test_parse_output_rejects_bad_parse_status():
    with pytest.raises(PydanticValidationError):
        ParseOutput.model_validate({"parse_status": "not_a_real_status"})


def test_diagnosis_output_rejects_bad_resolution_type():
    with pytest.raises(PydanticValidationError):
        DiagnosisOutput.model_validate({"resolution_type": "auto_execute"})


def test_diagnosis_output_confidence_accepts_float():
    out = DiagnosisOutput.model_validate({"confidence": 0.73, "resolution_type": "guidance"})
    assert out.confidence == pytest.approx(0.73)


def _valid_template_payload() -> dict:
    return {
        "incident_id": "i1",
        "bot_id": "b1",
        "job_run_id": "r1",
        "environment": "production",
        "error_category": "CONNECTIVITY",
        "summary": "ConnectionError while loading",
        "root_cause": "db down",
        "recommended_action": "retry",
        "evidence_refs": [{"type": "log", "ref": "1"}],
        "unknown_fields": [],
    }


def test_template_payload_pydantic_valid():
    TemplatePayload.model_validate(_valid_template_payload())


def test_template_payload_jsonschema_valid():
    validate_against_template_schema(_valid_template_payload())


def test_template_payload_jsonschema_rejects_missing_required_field():
    payload = _valid_template_payload()
    del payload["root_cause"]
    with pytest.raises(ValidationError):
        validate_against_template_schema(payload)


def test_template_payload_jsonschema_rejects_extra_field():
    payload = _valid_template_payload()
    payload["not_a_real_field"] = "x"
    with pytest.raises(ValidationError):
        validate_against_template_schema(payload)
