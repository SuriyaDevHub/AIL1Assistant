"""Jira REST client - doc section 7.2: "REST API issue creation, attachment,
search for deduplication and closure webhook for feedback".

RealJiraClient targets the standard Jira Cloud/Server REST v2 issue-search
and issue-create surface. MockJiraServer is a fully in-memory stand-in
(auto-incrementing issue keys, stores comments/attachments/status) used for
local runs and tests - doc's Integration Agent (5.6) and dedup logic (7.3)
are exercised against it end to end with zero external dependencies.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx


@dataclass
class JiraIssue:
    key: str
    summary: str
    description: str
    status: str = "Open"
    labels: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class JiraClient(ABC):
    @abstractmethod
    async def create_issue(
        self,
        *,
        project_key: str,
        summary: str,
        description: str,
        labels: list[str] | None = None,
        attachments: list[tuple[str, bytes]] | None = None,
    ) -> str:
        """Returns the created issue key."""

    @abstractmethod
    async def add_comment(self, issue_key: str, comment: str) -> None: ...

    @abstractmethod
    async def search_open_by_label(self, label: str, *, since: datetime) -> list[JiraIssue]:
        """Used by dedup (doc 7.3 step 2: exact match search)."""

    @abstractmethod
    async def search_closed(self, *, project_key: str, limit: int = 50) -> list[dict]:
        """Used by the KB build pipeline's Jira extractor for historical
        closed-ticket precedent (doc 4.1)."""

    @abstractmethod
    async def get_issue(self, issue_key: str) -> JiraIssue | None: ...


class MockJiraServer(JiraClient):
    def __init__(self, *, project_key: str = "GENIE", start_seq: int = 2000):
        self._project_key = project_key
        self._seq = start_seq
        self._issues: dict[str, JiraIssue] = {}

    async def create_issue(
        self,
        *,
        project_key: str,
        summary: str,
        description: str,
        labels: list[str] | None = None,
        attachments: list[tuple[str, bytes]] | None = None,
    ) -> str:
        self._seq += 1
        key = f"{project_key}-{self._seq}"
        self._issues[key] = JiraIssue(key=key, summary=summary, description=description, labels=labels or [])
        return key

    async def add_comment(self, issue_key: str, comment: str) -> None:
        issue = self._issues.get(issue_key)
        if issue is not None:
            issue.comments.append(comment)

    async def search_open_by_label(self, label: str, *, since: datetime) -> list[JiraIssue]:
        return [
            issue
            for issue in self._issues.values()
            if label in issue.labels and issue.status == "Open" and issue.created_at >= since
        ]

    async def search_closed(self, *, project_key: str, limit: int = 50) -> list[dict]:
        return [
            {"key": i.key, "summary": i.summary, "body": i.description}
            for i in self._issues.values()
            if i.status == "Closed"
        ][:limit]

    async def get_issue(self, issue_key: str) -> JiraIssue | None:
        return self._issues.get(issue_key)

    def close_issue(self, issue_key: str) -> None:
        """Test/demo helper - simulates a support engineer closing the
        ticket, which the feedback loop's webhook handler reacts to."""
        if issue_key in self._issues:
            self._issues[issue_key].status = "Closed"


class RealJiraClient(JiraClient):
    def __init__(self, *, base_url: str, user_email: str, api_token: str, http_client: httpx.AsyncClient | None = None):
        self._base_url = base_url.rstrip("/")
        self._auth = (user_email, api_token)
        self._http = http_client or httpx.AsyncClient(timeout=15.0, auth=self._auth)

    async def create_issue(
        self,
        *,
        project_key: str,
        summary: str,
        description: str,
        labels: list[str] | None = None,
        attachments: list[tuple[str, bytes]] | None = None,
    ) -> str:
        resp = await self._http.post(
            f"{self._base_url}/rest/api/2/issue",
            json={
                "fields": {
                    "project": {"key": project_key},
                    "summary": summary,
                    "description": description,
                    "issuetype": {"name": "Task"},
                    "labels": labels or [],
                }
            },
        )
        resp.raise_for_status()
        key = resp.json()["key"]

        for filename, content in attachments or []:
            await self._http.post(
                f"{self._base_url}/rest/api/2/issue/{key}/attachments",
                headers={"X-Atlassian-Token": "no-check"},
                files={"file": (filename, content)},
            )
        return key

    async def add_comment(self, issue_key: str, comment: str) -> None:
        resp = await self._http.post(
            f"{self._base_url}/rest/api/2/issue/{issue_key}/comment", json={"body": comment}
        )
        resp.raise_for_status()

    async def search_open_by_label(self, label: str, *, since: datetime) -> list[JiraIssue]:
        jql = f'labels = "{label}" AND status != Closed AND created >= "{since.strftime("%Y-%m-%d %H:%M")}"'
        resp = await self._http.get(f"{self._base_url}/rest/api/2/search", params={"jql": jql})
        resp.raise_for_status()
        return [
            JiraIssue(
                key=i["key"],
                summary=i["fields"]["summary"],
                description=i["fields"].get("description", ""),
                status=i["fields"]["status"]["name"],
                labels=i["fields"].get("labels", []),
            )
            for i in resp.json().get("issues", [])
        ]

    async def search_closed(self, *, project_key: str, limit: int = 50) -> list[dict]:
        jql = f'project = "{project_key}" AND status = Closed ORDER BY resolutiondate DESC'
        resp = await self._http.get(
            f"{self._base_url}/rest/api/2/search", params={"jql": jql, "maxResults": limit}
        )
        resp.raise_for_status()
        return [
            {"key": i["key"], "summary": i["fields"]["summary"], "body": i["fields"].get("description", "")}
            for i in resp.json().get("issues", [])
        ]

    async def get_issue(self, issue_key: str) -> JiraIssue | None:
        resp = await self._http.get(f"{self._base_url}/rest/api/2/issue/{issue_key}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        body = resp.json()
        return JiraIssue(
            key=body["key"],
            summary=body["fields"]["summary"],
            description=body["fields"].get("description", ""),
            status=body["fields"]["status"]["name"],
            labels=body["fields"].get("labels", []),
        )
