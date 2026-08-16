"""Diagnostic (L1 Triage) Agent - doc section 5.3."""
from __future__ import annotations

import json

from geniebot.agents.base import BaseAgent
from geniebot.schemas.diagnosis import DiagnosisOutput, DiagnosticAgentInput


class DiagnosticAgent(BaseAgent[DiagnosticAgentInput, DiagnosisOutput]):
    agent_name = "diagnostic"
    output_model = DiagnosisOutput

    def build_user_prompt(self, agent_input: DiagnosticAgentInput) -> str:
        template = self.prompt_config["user_template"]
        if agent_input.kb_chunks:
            kb_context = json.dumps(
                [
                    {
                        "source_ref": c.source_ref,
                        "content": c.content,
                        "similarity": c.similarity,
                        "effective_from": c.effective_from.isoformat(),
                    }
                    for c in agent_input.kb_chunks
                ]
            )
        else:
            kb_context = "no_precedent"

        return template.format(
            parse_output_json=agent_input.parse_output.model_dump_json(),
            kb_context=kb_context,
            top_n=len(agent_input.kb_chunks),
            error_category=agent_input.error_category or "unknown",
        )
