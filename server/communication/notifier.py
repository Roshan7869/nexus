#!/usr/bin/env python3
"""
NEXUS severity-based notification system.

Routes P0-P3 events to pluggable backends. External backends degrade to the
console logger when credentials or configuration are missing so that a missing
env var never raises an unhandled exception.
"""

from __future__ import annotations

import logging
import os
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.mime.text import MIMEText
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    """Incident severity levels."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class NotificationBackend(ABC):
    """Abstract notification backend."""

    name: str = "abstract"

    @abstractmethod
    def send(self, message: str, subject: str | None = None) -> bool:
        """Send *message*. Return True on success, False otherwise."""
        ...


class ConsoleBackend(NotificationBackend):
    """Log-only backend; always available."""

    name = "console"

    def send(self, message: str, subject: str | None = None) -> bool:
        logger.warning(f"[{self.name.upper()}] {subject or 'NOTIFY'}: {message}")
        return True


class TelegramBackend(NotificationBackend):
    """Telegram Bot API backend.

    Requires env vars ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID``.
    """

    name = "telegram"

    def __init__(self) -> None:
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        self._available = bool(self.token and self.chat_id)

    def send(self, message: str, subject: str | None = None) -> bool:
        if not self._available:
            logger.warning(
                "TelegramBackend disabled: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing"
            )
            return False

        assert self.token is not None and self.chat_id is not None
        text = f"{subject}\n\n{message}" if subject else message
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"}
        try:
            response = httpx.post(url, json=payload, timeout=15.0)
            response.raise_for_status()
            return True
        except Exception as exc:  # pragma: no cover - network failures
            logger.warning(f"Telegram send failed: {exc}")
            return False


class SlackBackend(NotificationBackend):
    """Slack incoming-webhook backend.

    Requires env var ``SLACK_WEBHOOK_URL``.
    """

    name = "slack"

    def __init__(self) -> None:
        self.webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
        self._available = bool(self.webhook_url)

    def send(self, message: str, subject: str | None = None) -> bool:
        if not self._available:
            logger.warning("SlackBackend disabled: SLACK_WEBHOOK_URL missing")
            return False

        assert self.webhook_url is not None
        payload: dict[str, Any] = {"text": message}
        if subject:
            payload["blocks"] = [
                {"type": "header", "text": {"type": "plain_text", "text": subject}},
                {"type": "section", "text": {"type": "mrkdwn", "text": message}},
            ]
        try:
            response = httpx.post(self.webhook_url, json=payload, timeout=15.0)
            response.raise_for_status()
            return True
        except Exception as exc:  # pragma: no cover - network failures
            logger.warning(f"Slack send failed: {exc}")
            return False


class EmailBackend(NotificationBackend):
    """SMTP email backend.

    Required env vars:
      - SMTP_HOST
      - SMTP_PORT
      - SMTP_USER
      - SMTP_PASSWORD
      - SMTP_FROM
      - SMTP_TO
    """

    name = "email"

    def __init__(self) -> None:
        self.host = os.environ.get("SMTP_HOST")
        self.port = int(os.environ.get("SMTP_PORT") or "0")
        self.user = os.environ.get("SMTP_USER")
        self.password = os.environ.get("SMTP_PASSWORD")
        self.from_addr = os.environ.get("SMTP_FROM")
        self.to_addrs = [a.strip() for a in (os.environ.get("SMTP_TO") or "").split(",") if a.strip()]
        self._available = all(
            [self.host, self.port, self.user, self.password, self.from_addr, self.to_addrs]
        )

    def send(self, message: str, subject: str | None = None) -> bool:
        if not self._available:
            logger.warning("EmailBackend disabled: SMTP_* settings incomplete")
            return False

        assert self.host is not None
        assert self.from_addr is not None
        assert self.user is not None
        assert self.password is not None

        msg = MIMEText(message)
        msg["Subject"] = subject or "NEXUS Notification"
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)

        try:
            with smtplib.SMTP(self.host, self.port, timeout=15) as server:
                server.starttls()
                server.login(self.user, self.password)
                server.sendmail(self.from_addr, self.to_addrs, msg.as_string())
            return True
        except Exception as exc:  # pragma: no cover - network failures
            logger.warning(f"Email send failed: {exc}")
            return False


class SMSBackend(NotificationBackend):
    """Generic HTTP SMS gateway backend.

    Uses env vars ``SMS_API_URL``, ``SMS_API_KEY``, and ``SMS_TO_NUMBER``.
    If any are missing the backend logs a warning and refuses to send.
    """

    name = "sms"

    def __init__(self) -> None:
        self.api_url = os.environ.get("SMS_API_URL")
        self.api_key = os.environ.get("SMS_API_KEY")
        self.to_number = os.environ.get("SMS_TO_NUMBER")
        self._available = bool(self.api_url and self.api_key and self.to_number)

    def send(self, message: str, subject: str | None = None) -> bool:
        if not self._available:
            logger.warning("SMSBackend disabled: SMS_API_URL/SMS_API_KEY/SMS_TO_NUMBER missing")
            return False

        assert self.api_url is not None and self.api_key is not None
        assert self.to_number is not None

        text = f"{subject}: {message}" if subject else message
        payload = {"to": self.to_number, "body": text[:1600]}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            response = httpx.post(self.api_url, json=payload, headers=headers, timeout=15.0)
            response.raise_for_status()
            return True
        except Exception as exc:  # pragma: no cover - network failures
            logger.warning(f"SMS send failed: {exc}")
            return False


@dataclass
class _BackendSpec:
    cls: type[NotificationBackend]
    severities: set[Severity]


class Notifier:
    """Severity-based notification router.

    Default routing:
      - P0 -> Telegram + SMS
      - P1 -> Slack
      - P2 -> Email
      - P3 -> Console (log only)

    Backends can be injected for testing via ``backends``.
    """

    _DEFAULT_ROUTING: dict[Severity, list[str]] = {
        Severity.P0: ["telegram", "sms"],
        Severity.P1: ["slack"],
        Severity.P2: ["email"],
        Severity.P3: ["console"],
    }

    _DEFAULT_REGISTRY: dict[str, type[NotificationBackend]] = {
        "console": ConsoleBackend,
        "telegram": TelegramBackend,
        "slack": SlackBackend,
        "email": EmailBackend,
        "sms": SMSBackend,
    }

    def __init__(
        self,
        backends: dict[str, NotificationBackend] | None = None,
        routing: dict[Severity, list[str]] | None = None,
    ) -> None:
        self.routing = routing or self._DEFAULT_ROUTING
        self.backends: dict[str, NotificationBackend] = backends or self._build_default_backends()

    def _build_default_backends(self) -> dict[str, NotificationBackend]:
        return {name: cls() for name, cls in self._DEFAULT_REGISTRY.items()}

    def notify(self, event: dict[str, Any]) -> dict[str, bool]:
        """Route an event to backends based on its ``severity`` field.

        Args:
            event: Must contain ``severity`` (P0-P3) and ``message``. Optional
                ``subject`` is used as the alert title.

        Returns:
            Mapping of backend name -> success boolean.
        """
        severity_str = event.get("severity", "P3")
        try:
            severity = Severity(severity_str)
        except ValueError:
            logger.warning(f"Unknown severity '{severity_str}', defaulting to P3")
            severity = Severity.P3

        message = event.get("message", "")
        subject = event.get("subject", f"NEXUS Alert [{severity.value}]")
        backend_names = self.routing.get(severity, ["console"])

        results: dict[str, bool] = {}
        for name in backend_names:
            backend = self.backends.get(name)
            if backend is None:
                logger.warning(f"No backend registered for '{name}', falling back to console")
                backend = ConsoleBackend()
            results[name] = backend.send(message, subject)
        return results

    def send_degraded_skill_alert(
        self, skill_name: str, reason: str, severity: str = "P0"
    ) -> dict[str, bool]:
        """Convenience helper for skill-degradation events."""
        event = {
            "severity": severity,
            "subject": f"Skill degraded: {skill_name}",
            "message": f"Skill '{skill_name}' was auto-degraded.\n\nReason: {reason}",
            "skill_name": skill_name,
            "reason": reason,
        }
        return self.notify(event)
