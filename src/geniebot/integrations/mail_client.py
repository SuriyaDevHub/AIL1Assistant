"""Mail dispatch - doc section 7.2: "Dispatch of alert and completed
template to the Genie Support Mailbox". RealSMTPClient works against both a
real SMTP relay and a local MailHog instance since MailHog speaks plain
SMTP with no auth on port 1025. MockMailClient captures sent messages
in-memory for local runs/tests with no SMTP server at all.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from email.message import EmailMessage

import aiosmtplib


@dataclass(frozen=True)
class SentMail:
    to: str
    subject: str
    body: str
    attachments: list[str] = field(default_factory=list)


class MailClient(ABC):
    @abstractmethod
    async def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        attachments: list[tuple[str, bytes]] | None = None,
    ) -> None: ...


class MockMailClient(MailClient):
    def __init__(self) -> None:
        self.sent: list[SentMail] = []

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        attachments: list[tuple[str, bytes]] | None = None,
    ) -> None:
        self.sent.append(
            SentMail(to=to, subject=subject, body=body, attachments=[a[0] for a in (attachments or [])])
        )


class RealSMTPClient(MailClient):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
        sender: str = "geniebot@internal",
    ):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        attachments: list[tuple[str, bytes]] | None = None,
    ) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        for filename, content in attachments or []:
            message.add_attachment(content, maintype="application", subtype="octet-stream", filename=filename)

        await aiosmtplib.send(
            message,
            hostname=self._host,
            port=self._port,
            username=self._username or None,
            password=self._password or None,
            use_tls=False,
        )
