"""Common agent contract - doc section 5.1:

- typed input in, typed output out - never free-form text between agents
- strict JSON, schema-validated before use
- schema validation failure -> one bounded retry with a corrective
  instruction -> fails closed to manual
- every invocation records prompt version, model, token counts and latency
  to the audit ledger
- low temperature for deterministic extraction/diagnosis

LLM platform errors (LLMPlatformError) are NOT retried here - they
propagate immediately so the orchestration layer can route the incident to
PLATFORM_UNAVAILABLE -> MANUAL_FALLBACK (doc 1.4 "fail closed"). Only
JSON-parse / schema-validation failures get the one bounded retry, since
those are plausibly fixable with a corrective instruction on the same call.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from geniebot.llm.client import LLMClient
from geniebot.settings import get_settings

TIn = TypeVar("TIn")
TOut = TypeVar("TOut", bound=BaseModel)

MAX_ATTEMPTS = 2  # one bounded retry, per doc 5.1

_CORRECTIVE_SUFFIX = (
    "\n\nYour previous response was not valid JSON matching the required schema exactly. "
    "Respond again with STRICT JSON ONLY - no prose, no markdown code fences, no explanation - "
    "matching the schema exactly."
)


class AgentSchemaValidationError(RuntimeError):
    """Raised after MAX_ATTEMPTS schema-validation failures. Callers must
    treat this as fail-closed to MANUAL_FALLBACK."""


@dataclass(frozen=True)
class AgentInvocationRecord:
    agent_name: str
    prompt_version: int
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    attempt: int
    success: bool
    error: str | None = None

    def audit_payload(self) -> dict:
        return {
            "agent_name": self.agent_name,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "latency_ms": round(self.latency_ms, 2),
            "attempt": self.attempt,
            "success": self.success,
            "error": self.error,
        }


class BaseAgent(ABC, Generic[TIn, TOut]):
    agent_name: str
    output_model: type[TOut]

    def __init__(self, llm_client: LLMClient, prompt_config: dict):
        self.llm = llm_client
        self.prompt_config = prompt_config

    @abstractmethod
    def build_user_prompt(self, agent_input: TIn) -> str: ...

    async def run(self, agent_input: TIn) -> tuple[TOut, list[AgentInvocationRecord]]:
        records: list[AgentInvocationRecord] = []
        system = self.prompt_config["system"]
        user = self.build_user_prompt(agent_input)
        # settings.generation_model, when set, overrides every prompt's own
        # pinned model in one place - e.g. to point the whole pipeline at
        # an internal AI platform's model without editing every prompt
        # file. Empty (the default) keeps each prompt's own pinned model.
        model = get_settings().generation_model or self.prompt_config.get("model", "gpt-5.1")
        temperature = self.prompt_config.get("temperature", 0.0)
        max_output_tokens = self.prompt_config.get("max_output_tokens", 1500)
        prompt_version = self.prompt_config.get("version", 1)

        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            effective_system = system if attempt == 1 else system + _CORRECTIVE_SUFFIX
            response = await self.llm.chat_completion(
                system=effective_system,
                user=user,
                model=model,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )

            try:
                data = json.loads(response.content)
                output = self.output_model.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                records.append(
                    AgentInvocationRecord(
                        agent_name=self.agent_name,
                        prompt_version=prompt_version,
                        model=response.model,
                        prompt_tokens=response.prompt_tokens,
                        completion_tokens=response.completion_tokens,
                        latency_ms=response.latency_ms,
                        attempt=attempt,
                        success=False,
                        error=str(exc),
                    )
                )
                continue

            records.append(
                AgentInvocationRecord(
                    agent_name=self.agent_name,
                    prompt_version=prompt_version,
                    model=response.model,
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    latency_ms=response.latency_ms,
                    attempt=attempt,
                    success=True,
                )
            )
            return output, records

        raise AgentSchemaValidationError(
            f"{self.agent_name} failed schema validation after {MAX_ATTEMPTS} attempts: {last_error}"
        )
