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
    llm_backend: Literal["mock", "internal_platform"] = Field(default="mock", alias="LLM_BACKEND")
    internal_ai_platform_base_url: str = Field(
        default="https://internal-ai-platform.example.bank/v1",
        alias="INTERNAL_AI_PLATFORM_BASE_URL",
    )
    generation_model: str = Field(default="gpt-5.1", alias="GENERATION_MODEL")
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
    vector_store_backend: Literal["pgvector", "memory"] = Field(
        default="pgvector", alias="VECTOR_STORE_BACKEND"
    )

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
def get_prompt_config(name: str) -> dict:
    """name e.g. 'log_parser.v1.yaml' - see config/prompts/."""
    return _load_yaml(f"prompts/{name}")


def clear_config_cache() -> None:
    """Called by the admin config-update endpoint after writing new YAML."""
    get_thresholds.cache_clear()
    get_guardrail_config.cache_clear()
    get_taxonomy.cache_clear()
    get_prompt_config.cache_clear()
