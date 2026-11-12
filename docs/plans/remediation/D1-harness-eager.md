# D1 — Harness eager by default

Source: plan §D1 · Commit: `D1: harness eager by default` · Depends on: A1 · Gate: G9

## Problem (verified)

- `server/router_cutover.py:277` — `eager = os.environ.get("NEXUS_HARNESS_EAGER", "false").lower() in (…)`. Plan's line 277 is correct.
- `supervisord.conf` — `[program:…]` blocks: `nexus-http-daemon` (47), `nexus-mcp-server` (65), `nexus-dashboard` (80), `nexus-context-harness` (96), `nexus-router-env` (113). The MCP program already sets several `NEXUS_*` env vars (line 73).

## Corrections to source plan

| Plan says | Reality |
|---|---|
| Change env default in `router_cutover.py` | Correct. But `CLAUDE.md` says feature flags go through `core.config.LearningConfig`, not `os.environ` directly. Add the flag there and read it from there. |
| Add `NEXUS_HARNESS_EAGER="true"` to "all environment lines" | Add to each `environment=` line that starts a router-using process (MCP and HTTP). Not dashboard. |
| Verify with `curl … :8080/nexus/route` | Use **`:8000`**. |

## Pseudocode (corrected)

```python
# core/config.py  (LearningConfig)
harness_eager: bool = True            # env: NEXUS_HARNESS_EAGER (YAML > env > default)
```

```python
# server/router_cutover.py  (replace line 277 env read)
from core.config import LearningConfig
eager = LearningConfig.load().harness_eager
```

```ini
; supervisord.conf — nexus-mcp-server and nexus-http-daemon environment= lines
environment=...,NEXUS_HARNESS_EAGER="true"
```

## Steps

1. Read `core/config.py` to confirm how `LearningConfig` loads env vars. Match the existing pattern.
2. Add `harness_eager` with default `True`.
3. Replace the `router_cutover.py:277` read.
4. Edit `supervisord.conf` (MCP and HTTP programs only). **Do not** change `autostart` here — that is E1.
5. Measure latency before and after (see benchmark).

## Tests

New `tests/test_harness_eager.py`:

```python
from core.config import LearningConfig

def test_default_is_eager(monkeypatch):
    monkeypatch.delenv("NEXUS_HARNESS_EAGER", raising=False)
    assert LearningConfig.load().harness_eager is True

def test_env_can_disable(monkeypatch):
    monkeypatch.setenv("NEXUS_HARNESS_EAGER", "false")
    assert LearningConfig.load().harness_eager is False
```

Benchmark (plan risk D2 — latency): time `route_for_task` 20 times with flag on and off, same DB. Record p50 and p95. Plan's D2 risk says harness adds hot-path latency; this change makes it default, so measure first.

## Verification (live)

```bash
curl -s -X POST http://127.0.0.1:8000/nexus/route -H "Content-Type: application/json" \
  -d '{"task":"deploy to prod","project":"nexus"}' | python3 -m json.tool | grep -i harness
```

Expected: a `harness` key in the response (G9).

## Rollback

`export NEXUS_HARNESS_EAGER=false` and restart (plan's rollback table).

## Open decisions

- Accept the latency cost of eager harness on every route? Measure first; if p95 > 300 ms (plan's target), use the 60 s per-(task, project) cache from D2.
