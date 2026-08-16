from __future__ import annotations

from functools import lru_cache

from geniebot.integrations.jira_client import JiraClient, MockJiraServer, RealJiraClient
from geniebot.integrations.mail_client import MailClient, MockMailClient, RealSMTPClient
from geniebot.settings import get_settings


@lru_cache
def get_jira_client() -> JiraClient:
    settings = get_settings()
    if settings.jira_backend == "real":
        return RealJiraClient(
            base_url=settings.jira_base_url,
            user_email=settings.jira_user_email,
            api_token=settings.jira_api_token,
        )
    return MockJiraServer(project_key=settings.jira_project_key)


@lru_cache
def get_mail_client() -> MailClient:
    settings = get_settings()
    if settings.mail_backend == "smtp":
        return RealSMTPClient(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
        )
    return MockMailClient()
