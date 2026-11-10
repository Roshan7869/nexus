"""T2-6: scrapecraft key is env-only and sent only to allowlisted hosts."""
from __future__ import annotations

from server.context_harness.channels import scrapecraft_pattern as sp


def test_key_not_read_from_bashrc(monkeypatch, tmp_path):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".bashrc").write_text(
        'export OLLAMA_API_KEY="from-bashrc"\nexport OPENAI_API_KEY="openai-from-bashrc"\n'
    )
    assert sp._load_ollama_key() == ""


def test_key_read_from_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "env-key")
    assert sp._load_ollama_key() == "env-key"


def test_local_placeholder_is_not_a_key(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "local")
    assert sp._load_ollama_key() == ""


def test_key_allowlist():
    assert sp._key_allowed_for("https://ollama.com/api/chat")
    assert not sp._key_allowed_for(sp._LLM_URL)
    assert not sp._key_allowed_for("https://evil.example/ollama.com")


async def test_authorization_not_sent_to_localhost(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "env-key")
    captured: dict = {}

    class FakeResp:
        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            return FakeResp()

    monkeypatch.setattr(sp.httpx, "AsyncClient", FakeClient)
    extractor = sp.SemanticExtractor()
    extractor._ollama_key = "env-key"
    await extractor.extract("https://example.com/", ["title"], html="<html></html>")
    assert captured["url"] == sp._LLM_URL
    assert "Authorization" not in captured["headers"]
