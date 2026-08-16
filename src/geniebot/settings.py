"""Central application settings.

Every backend selector below defaults to a local/mock implementation so the
whole stack runs with zero configuration. Flipping a *_BACKEND value to a
real one (and supplying the matching credentials) is the only change needed
to cut over to real bank infrastructure - see docs/runbook.md and the
"production cutover checklist" in README.md.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    env: Literal["local", "uat", "production"] = Field(default="local", alias="GENIEBOT_ENV")

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://geniebot:geniebot@localhost:5432/geniebot",
        alias="DATABASE_URL",
    )

    # Queue
    queue_backend: Literal["memory", "sqs"] = Field(default="memory", alias="QUEUE_BACKEND")
    sqs_queue_url: str = Field(default="", alias="SQS_QUEUE_URL")
    sqs_dlq_url: str = Field(default="", alias="SQS_DLQ_URL")

    # Storage
    storage_backend: Literal["local", "s3"] = Field(default="local", alias="STORAGE_BACKEND")
    s3_bucket: str = Field(default="genie-bot-logs", alias="S3_BUCKET")
    s3_working_prefix: str = Field(default="working/", alias="S3_WORKING_PREFIX")
    local_s3_root: str = Field(default="./local_s3", alias="LOCAL_S3_ROOT")

    # LLM
    llm_backend: Literal["mock", "internal_platform", "openai", "anthropic"] = Field(
        default="mock", alias="LLM_BACKEND"
    )
    internal_ai_platform_base_url: str = Field(
        default="https://internal-ai-platform.example.bank/v1",
        alias="INTERNAL_AI_PLATFORM_BASE_URL",
    )
    # Optional path to a CA bundle (PEM file) for a restricted network whose
    # internal FQDNs present certs signed by an internal CA not in the
    # system trust store. Empty = use the default trust store. Passed
    # straight to httpx's `verify=` - covers both the chat/embeddings calls
    # (llm/internal_platform_client.py) and the OpenAM/DSP token calls
    # (llm/auth.py's OpenAMDSPTokenProvider), since both typically live on
    # the same internal network.
    internal_ai_platform_ca_bundle: str = Field(default="", alias="INTERNAL_AI_PLATFORM_CA_BUNDLE")
    # openai: a real, publicly-reachable OpenAI-compatible backend (OpenAI
    # itself, Azure OpenAI, a self-hosted server) authenticated with a plain
    # API key rather than OpenAM/DSP - see llm/auth.py's StaticTokenProvider
    # and llm/factory.py. Distinct from internal_platform above, which is
    # the doc-described bank platform behind OpenAM.
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    # anthropic: the real Anthropic Messages API - a different wire format
    # from the OpenAI-compatible client above, so it has its own client
    # class (llm/anthropic_client.py). No embeddings endpoint, so KB
    # retrieval falls back to the local deterministic embedding either way.
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    # No /v1 suffix - unlike the OpenAI-compatible base_url above, the
    # official anthropic SDK appends /v1/... itself; including /v1 here
    # double-prefixes the path and 404s.
    anthropic_base_url: str = Field(default="https://api.anthropic.com", alias="ANTHROPIC_BASE_URL")
    # Empty by default: each agent's prompt config (config/prompts/*.yaml
    # `model:` field) pins its own model, since a prompt is often tuned
    # against one specific model's response style. Set GENERATION_MODEL to
    # override every agent to one model in one place - e.g. to switch the
    # whole pipeline onto whatever your internal AI platform serves,
    # without hand-editing all three prompt files. See agents/base.py.
    generation_model: str = Field(default="", alias="GENERATION_MODEL")
    embedding_model: str = Field(default="text-embedding-small", alias="EMBEDDING_MODEL")

    # Auth
    auth_backend: Literal["mock", "openam"] = Field(default="mock", alias="AUTH_BACKEND")
    openam_token_url: str = Field(default="", alias="OPENAM_TOKEN_URL")
    dsp_translate_url: str = Field(default="", alias="DSP_TRANSLATE_URL")
    openam_client_id: str = Field(default="", alias="OPENAM_CLIENT_ID")
    openam_client_secret: str = Field(default="", alias="OPENAM_CLIENT_SECRET")
    jwt_jwks_url: str = Field(default="", alias="JWT_JWKS_URL")
    jwt_audience: str = Field(default="geniebot-api", alias="JWT_AUDIENCE")
    api_dev_shared_secret: str = Field(
        default="dev-only-shared-secret-change-me", alias="API_DEV_SHARED_SECRET"
    )

    # Jira
    jira_backend: Literal["mock", "real"] = Field(default="mock", alias="JIRA_BACKEND")
    jira_base_url: str = Field(default="https://jira.example.bank", alias="JIRA_BASE_URL")
    jira_project_key: str = Field(default="GENIE", alias="JIRA_PROJECT_KEY")
    jira_api_token: str = Field(default="", alias="JIRA_API_TOKEN")
    jira_user_email: str = Field(default="", alias="JIRA_USER_EMAIL")

    # Mail
    mail_backend: Literal["mock", "smtp"] = Field(default="mock", alias="MAIL_BACKEND")
    smtp_host: str = Field(default="localhost", alias="SMTP_HOST")
    smtp_port: int = Field(default=1025, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    genie_support_mailbox: str = Field(
        default="genie-support@example.bank", alias="GENIE_SUPPORT_MAILBOX"
    )

    # Secrets
    secrets_backend: Literal["env", "approved_secrets_manager"] = Field(
        default="env", alias="SECRETS_BACKEND"
    )
    secrets_manager_endpoint: str = Field(default="", alias="SECRETS_MANAGER_ENDPOINT")

    # Vector store
    vector_store_backend: Literal["pgvector", "memory", "faiss"] = Field(
        default="pgvector", alias="VECTOR_STORE_BACKEND"
    )
    # faiss: a local, file-based, restart-durable index - an interim step
    # for an environment without a provisioned pgvector database yet.
    # Single-process only (kb/faiss_vector_store.py's module docstring) -
    # same constraint "memory" already has today, just persisted to disk.
    faiss_index_dir: str = Field(default="./local_faiss_index", alias="FAISS_INDEX_DIR")

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache
def get_thresholds() -> dict:
    return _load_yaml("thresholds.yaml")


@lru_cache
def get_guardrail_config() -> dict:
    return _load_yaml("guardrails.yaml")


@lru_cache
def get_taxonomy() -> dict:
    return _load_yaml("taxonomy.yaml")


@lru_cache
def get_generic_l1_checklist() -> dict:
    return _load_yaml("generic_l1_checklist.yaml")


@lru_cache
def get_prompt_config(name: str) -> dict:
    """name e.g. 'log_parser.v1.yaml' - see config/prompts/."""
    return _load_yaml(f"prompts/{name}")


def clear_config_cache() -> None:
    """Called by the admin config-update endpoint after writing new YAML."""
    get_thresholds.cache_clear()
    get_guardrail_config.cache_clear()
    get_taxonomy.cache_clear()
    get_generic_l1_checklist.cache_clear()
    get_prompt_config.cache_clear()
