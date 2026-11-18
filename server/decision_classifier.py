"""Typed decision classifiers for NEXUS routing decisions.

Two backends behind one interface:

* ``TypeSafeJevClassifier`` — TypeSafe "System One" (``/v1/systemone``): a hosted
  single-pass classifier that answers *typed questions* about a state blob. This is
  the fast inner loop: per-request labels, constrained to a fixed answer set.
  **Requires an API key.** Credentials resolve exactly like ``~/.config/jev/jev.py``:
  ``TYPESAFE_API_KEY`` first, then line 1 of ``~/.config/jev/api_key``.
* ``LocalHeuristicClassifier`` — no network, no key. Embedding-cosine and rule
  based. It **declines** rather than guessing when it is not confident.

Contract for callers: ``classify(...)`` always returns a :class:`TypedAnswer`; it
never raises for a missing key, a network error, or a malformed response. A declined
answer has ``value is None`` and a ``reason``, so routing can fall through instead of
acting on an invented label.

IMPORTANT — unverified surface: the ``/v1/systemone`` request shape below is taken
from the existing ``~/.config/jev/jev.py`` client (``model``, ``state``,
``questions[name] = {type, instructions}``). The *answer vocabulary and the exact
question ``type`` names are not verifiable without a key*, so both are configurable
via ``NEXUS_JEV_QUESTION_TYPE`` / ``NEXUS_JEV_MODEL`` and every parsed answer is
re-validated against the expected options.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence

JEV_BASE = os.environ.get("NEXUS_JEV_BASE", "https://api.typesafe.ai")
JEV_MODEL = os.environ.get("NEXUS_JEV_MODEL", "jev-latest")
JEV_KEY_FILE = Path.home() / ".config" / "jev" / "api_key"

#: Default question type. `noul` is the one type observed in the existing client;
#: override when the real schema is confirmed.
DEFAULT_QUESTION_TYPE = os.environ.get("NEXUS_JEV_QUESTION_TYPE", "noul")


@dataclass
class TypedAnswer:
    """One classifier verdict. `value is None` means "declined", never "unknown=yes"."""
    value: str | None
    confidence: float = 0.0
    source: str = "local"
    reason: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.value is not None


class DecisionClassifier(Protocol):
    def available(self) -> bool: ...
    def classify(self, kind: str, state: str, options: Sequence[str],
                 instructions: str = "") -> TypedAnswer: ...


# ── JEV / TypeSafe ───────────────────────────────────────────────────────────

def jev_api_key() -> str | None:
    key = (os.environ.get("TYPESAFE_API_KEY") or "").strip()
    if key:
        return key
    try:
        if JEV_KEY_FILE.is_file():
            first = JEV_KEY_FILE.read_text(encoding="utf-8").strip().splitlines()
            if first and first[0].strip():
                return first[0].strip()
    except OSError:
        pass
    return None


class TypeSafeJevClassifier:
    """Hosted System One classifier. Dormant (``available() == False``) without a key."""

    def __init__(self, model: str = JEV_MODEL, timeout: float = 20.0) -> None:
        self.model = model
        self.timeout = timeout
        self._key = jev_api_key()

    def available(self) -> bool:
        return bool(self._key)

    def classify(self, kind: str, state: str, options: Sequence[str],
                 instructions: str = "") -> TypedAnswer:
        if not self._key:
            return TypedAnswer(None, source="jev", reason="no_api_key")
        question = {
            "type": DEFAULT_QUESTION_TYPE,
            "instructions": instructions or (
                f"Answer must be exactly one of: {', '.join(options)}"),
            # Options are carried in the prompt text because the SDK-level enum
            # surface is unverified; the response is validated against them below.
            "options": list(options),
        }
        payload = {"model": self.model, "state": state, "questions": {kind: question}}
        req = urllib.request.Request(
            f"{JEV_BASE}/v1/systemone",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as exc:
            return TypedAnswer(None, source="jev", reason=f"transport:{type(exc).__name__}")

        value = _extract_answer(data, kind)
        if value is None:
            return TypedAnswer(None, source="jev", reason="unparseable", raw=data if isinstance(data, dict) else {})
        lowered = str(value).strip().lower()
        for opt in options:
            if lowered == opt.lower():
                return TypedAnswer(opt, confidence=1.0, source="jev", raw=data)
        # A value outside the permitted set is a refusal, not a guess.
        return TypedAnswer(None, source="jev", reason=f"out_of_set:{lowered[:40]}",
                           raw=data if isinstance(data, dict) else {})


def _extract_answer(data: object, kind: str) -> str | None:
    """Best-effort extraction across plausible System One response shapes."""
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return None
    for key in (kind, "answers", "questions", "result", "output"):
        node = data.get(key)
        if isinstance(node, str):
            return node
        if isinstance(node, dict):
            # nested per-question shape: {"answers": {"cluster": {"value": "..."}}}
            inner_node = node.get(kind)
            if isinstance(inner_node, dict):
                for inner in ("value", "answer", "label", "class"):
                    if isinstance(inner_node.get(inner), str):
                        return inner_node[inner]
            for inner in ("value", "answer", "label", "class", kind):
                if isinstance(node.get(inner), str):
                    return node[inner]
    for inner in ("value", "answer", "label"):
        if isinstance(data.get(inner), str):
            return data[inner]
    return None


# ── local, no-key backend ────────────────────────────────────────────────────

class LocalHeuristicClassifier:
    """Deterministic fallback. Declines when its signal is weak."""

    def __init__(self, threshold: float = 0.35) -> None:
        self.threshold = threshold

    def available(self) -> bool:
        return True

    def classify(self, kind: str, state: str, options: Sequence[str],
                 instructions: str = "") -> TypedAnswer:
        if kind == "intent":
            return self._intent(state, options)
        return TypedAnswer(None, source="local", reason=f"no_local_rule_for:{kind}")

    def _intent(self, state: str, options: Sequence[str]) -> TypedAnswer:
        """Keyword intent classifier with an explicit decline path."""
        text = (state or "").lower()
        rules = {
            "research": ("research", "paper", "survey", "literature", "study", "benchmark"),
            "coding": ("code", "implement", "refactor", "function", "bug", "fix", "test"),
            "ops": ("deploy", "docker", "pipeline", "ci", "infra", "cluster"),
            "docs": ("document", "readme", "docstring", "write up"),
            "debug": ("debug", "trace", "root cause", "diagnose", "why is"),
        }
        scores = {opt: 0 for opt in options}
        for intent, words in rules.items():
            for opt in options:
                if opt.lower().startswith(intent):
                    scores[opt] = sum(1 for w in words if w in text)
        best = max(scores, key=lambda k: scores[k])
        total = sum(scores.values())
        if total == 0 or scores[best] == 0:
            return TypedAnswer(None, source="local", reason="no_keyword_signal")
        confidence = scores[best] / total
        if confidence < self.threshold:
            return TypedAnswer(None, source="local", reason=f"low_confidence:{confidence:.2f}")
        return TypedAnswer(best, confidence=confidence, source="local")


# ── factory ──────────────────────────────────────────────────────────────────

_CACHE: dict[str, DecisionClassifier] = {}


def get_classifier(prefer: str | None = None) -> DecisionClassifier:
    """Resolve the classifier once. `prefer` defaults to NEXUS_DECISION_CLASSIFIER.

    `auto` prefers JEV when a key exists, otherwise the local backend. A missing key
    is never an error: it selects the fallback.
    """
    want = (prefer or os.environ.get("NEXUS_DECISION_CLASSIFIER") or "auto").strip().lower()
    cached = _CACHE.get(want)
    if cached is not None:
        return cached

    if want == "jev":
        chosen: DecisionClassifier = TypeSafeJevClassifier()
    elif want == "local":
        chosen = LocalHeuristicClassifier()
    else:  # auto
        jev = TypeSafeJevClassifier()
        chosen = jev if jev.available() else LocalHeuristicClassifier()

    _CACHE[want] = chosen
    return chosen
