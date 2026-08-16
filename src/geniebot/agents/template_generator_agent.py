"""Template Generator Agent - doc section 5.5.

Output is additionally validated against config/template_schema.json (doc
5.5: "validated against the approved template schema before it reaches the
reviewer") on top of the pydantic TemplatePayload validation that
BaseAgent.run() already performs - see
geniebot.schemas.template.validate_against_template_schema, called by the
caller (orchestration/pipeline.py) after this agent returns.
"""
from __future__ import annotations

import json

from geniebot.agents.base import BaseAgent
from geniebot.schemas.template import TemplateAgentInput, TemplatePayload


class TemplateGeneratorAgent(BaseAgent[TemplateAgentInput, TemplatePayload]):
    agent_name = "template_generator"
    output_model = TemplatePayload

    def build_user_prompt(self, agent_input: TemplateAgentInput) -> str:
        template = self.prompt_config["user_template"]
        precedents = json.dumps(
            [
                {"source_ref": c.source_ref, "content": c.content}
                for c in agent_input.template_precedents
            ]
        )
        return template.format(
            incident_id=agent_input.incident_id,
            bot_id=agent_input.bot_id,
            job_run_id=agent_input.job_run_id,
            environment=agent_input.environment,
            log_s3_uri=agent_input.log_s3_uri,
            parse_output_json=agent_input.parse_output.model_dump_json(),
            diagnosis_json=agent_input.diagnosis.model_dump_json(),
            template_precedents=precedents,
        )
