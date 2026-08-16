"""Log Parser Agent - doc section 5.2."""
from __future__ import annotations

from geniebot.agents.base import BaseAgent
from geniebot.schemas.parser import ParseOutput, ParserAgentInput


def _numbered(log_text: str) -> str:
    return "\n".join(f"{i + 1}: {line}" for i, line in enumerate(log_text.splitlines()))


class LogParserAgent(BaseAgent[ParserAgentInput, ParseOutput]):
    agent_name = "log_parser"
    output_model = ParseOutput

    def build_user_prompt(self, agent_input: ParserAgentInput) -> str:
        template = self.prompt_config["user_template"]
        return template.format(
            bot_id=agent_input.bot_id,
            job_run_id=agent_input.job_run_id,
            environment=agent_input.environment,
            redacted_log=_numbered(agent_input.redacted_log),
        )
