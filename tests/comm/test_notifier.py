"""Tests for the NEXUS severity-based notifier."""

from __future__ import annotations

from typing import Any

import pytest

from server.communication.notifier import (
    ConsoleBackend,
    EmailBackend,
    Notifier,
    NotificationBackend,
    Severity,
    SlackBackend,
    SMSBackend,
    TelegramBackend,
)


class _SpyBackend(NotificationBackend):
    """Test double that records send() invocations."""

    def __init__(self, name: str, succeed: bool = True) -> None:
        self.name = name
        self.succeed = succeed
        self.calls: list[tuple[str, str | None]] = []

    def send(self, message: str, subject: str | None = None) -> bool:
        self.calls.append((message, subject))
        return self.succeed


def test_p0_routes_to_telegram_and_sms() -> None:
    telegram = _SpyBackend("telegram")
    sms = _SpyBackend("sms")
    notifier = Notifier(backends={"telegram": telegram, "sms": sms, "console": ConsoleBackend()})

    results = notifier.notify({"severity": "P0", "message": "skill degraded", "subject": "ALERT"})

    assert results == {"telegram": True, "sms": True}
    assert telegram.calls == [("skill degraded", "ALERT")]
    assert sms.calls == [("skill degraded", "ALERT")]


def test_missing_env_falls_back_to_console() -> None:
    """When an external backend lacks credentials it returns False and logs a warning."""
    backend = TelegramBackend()
    assert backend._available is False

    # Direct send() with missing credentials returns False rather than raising.
    assert backend.send("test") is False

    # ConsoleBackend is always available and acts as the P3 fallback.
    console = _SpyBackend("console")
    notifier = Notifier(backends={"telegram": TelegramBackend(), "console": console})
    results = notifier.notify({"severity": "P3", "message": "info only"})

    assert results == {"console": True}
    assert console.calls == [("info only", "NEXUS Alert [P3]")]


def test_severity_routing() -> None:
    telegram = _SpyBackend("telegram")
    sms = _SpyBackend("sms")
    slack = _SpyBackend("slack")
    email = _SpyBackend("email")
    console = _SpyBackend("console")

    notifier = Notifier(
        backends={
            "telegram": telegram,
            "sms": sms,
            "slack": slack,
            "email": email,
            "console": console,
        }
    )

    notifier.notify({"severity": "P0", "message": "p0"})
    assert len(telegram.calls) == 1
    assert len(sms.calls) == 1

    notifier.notify({"severity": "P1", "message": "p1"})
    assert len(slack.calls) == 1

    notifier.notify({"severity": "P2", "message": "p2"})
    assert len(email.calls) == 1

    notifier.notify({"severity": "P3", "message": "p3"})
    assert len(console.calls) == 1


def test_send_degraded_skill_alert() -> None:
    telegram = _SpyBackend("telegram")
    sms = _SpyBackend("sms")
    notifier = Notifier(backends={"telegram": telegram, "sms": sms, "console": ConsoleBackend()})

    notifier.send_degraded_skill_alert("skill-name", "too many errors")

    assert telegram.calls[0][0].startswith("Skill 'skill-name' was auto-degraded")
    assert "too many errors" in telegram.calls[0][0]
    assert telegram.calls[0][1] == "Skill degraded: skill-name"
    assert sms.calls == telegram.calls


def test_unknown_severity_defaults_to_p3() -> None:
    console = _SpyBackend("console")
    notifier = Notifier(backends={"console": console})

    results = notifier.notify({"severity": "P9", "message": "weird"})

    assert results == {"console": True}
    assert console.calls[0] == ("weird", "NEXUS Alert [P3]")
