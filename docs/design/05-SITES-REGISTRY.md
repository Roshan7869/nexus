# NEXUS — Harness Sites Registry (per use case)

Status: DRAFT v1 · 2026-09-13
Companions: `00-HLD.md` §4 (S3, S5), `01-LLD.md` §3 (S3), `03-PLAN-MICRO.md` (`I5-1`, `I5-2`)

**This is the file you edit.** Everything below the "REGISTRY" line is yours to fill in.

---

## 1. How a site plugs in (three steps, no `server.py` edit)

The abstraction already exists. `server/context_harness/backend_protocol.py` defines the contract:

```python
@runtime_checkable
class BackendProtocol(Protocol):
    name: str
    weight: float
    cache_ttl: float
    timeout_seconds: float
    async def search(self, query: str, max_results: int = 10) -> list[HarnessResult]: ...
    async def is_available(self) -> bool: ...
```

and `fuse_registered()` runs every registered site **in parallel** with timeout, dedup, weight, and
error isolation, fusing by RRF (`RRF_K = 60.0`).

```
1. CHANNEL     write server/context_harness/channels/<your_site>.py
               (subclass BackendProtocol, or wrap an HTTP call in search())
2. REGISTRY    add a YAML entry to config/harness_sites.yaml       <- THIS FILE
3. BINDING     attach the site to one or more use cases in the same file
```

Today 13 sites are registered **inside** `server/context_harness/server.py:918-1188` — a code edit per
site. `I5-1`/`I5-2` move registration into the YAML so adding a site is configuration.

### What `search()` must return

`list[HarnessResult]` (`server/context_harness/models.py`) with at minimum:
`source` (str), `content` (str), `score` (float). `metadata` carries `url` and anything else you want
to keep. See the live example — `scrapling_fetch` in `server.py:910-919` returns exactly this shape.

### Weighting

Weights are **relative**, verified against the live registrations: `scrapling_fetch` = **1.2**,
`nexus`/`internet`/`memory` = **1.0** (`fusion.py:158-160`). A site you trust more than a generic
web fetch wants >1.2; a speculative site wants <1.0. RRF uses `RRF_K = 60.0`, so weights scale rank
contributions rather than raw scores — a large weight does not overwhelm a rank gap.

---

## 2. Sites already registered (13) — do not re-add these

`scrapling_fetch` (1.2) · `scrapling_spider` · `scrapecraft_pattern` · `sherlock_probe` ·
`target_site` · `arxiv` · `semantic_scholar` · `github` · `paperswithcode` · `google_scholar` ·
`openalex` · `crossref` · `pubmed`

Channel modules live in `server/context_harness/channels/` (15 files). `target_site` is the generic
entry point — it is what you specialise per use case.

---

## 3. Use cases

Use-case names bind to `ProjectType` (`server/context_harness/project_types.py`), which already
classifies by keyword signature:

| `ProjectType` | value | existing keyword signature |
|---|---|---|
| `AI_ML` | `ai-ml` | tensorflow, pytorch, llm, rag, embedding, transformer, … |
| `WEB_DEV` | `web-dev` | react, vue, nextjs, css, typescript, api, … |
| `GENERAL_DEV` | `general-dev` | — |
| `RESEARCH` | `research` | — |
| `DEVOPS` | `devops` | — |
| `TRADING` | `trading` | — |

**Your request mapped onto this:** "for frontend use getdesign.md" → the `web-dev` use case, with your
design site as an additional registered site. A site bound to `web-dev` is only consulted for tasks
the classifier places in `web-dev`, so a design-specific site does not pollute backend queries.

---

## 4. REGISTRY — `config/harness_sites.yaml`

> **Status:** the loader exists at `server/harness_sites.py` (ticket `I5-2`). Entries here
> are documentation and must be mirrored by a `fuser.register(BackendDef(...))` call. Write the YAML
> as the source of truth anyway; `I5-2` makes it executable.

```yaml
# config/harness_sites.yaml
# Sites are consulted only for the use cases they are bound to.
# weight: relative to scrapling_fetch=1.2 (live). cache_ttl/timeout in seconds.

defaults:
  weight: 1.0
  cache_ttl: 300
  timeout_seconds: 20
  max_results: 10

sites:

  # ─────────────── WEB / FRONTEND  (frontend_ui cluster: 576 resources) ───────────────
  # YOUR SITES GO HERE. I have left the getdesign.md case as a worked slot.
  web-dev:

    - name: getdesign
      channel: channels/getdesign.py
      weight: 1.4            # design-specific, trusted above generic fetch
      cache_ttl: 3600
      timeout_seconds: 25
      use_cases: [web-dev]
      notes: >
        PLACEHOLDER — I do not know your site. Replace `channel` with the fetch
        implementation. Intended role: design references and layout patterns for
        frontend tasks. Bind to `web-dev` only.
      # --- what to fill in ---
      # base_url:            <your site>
      # auth:                none | header:<NAME> | query:<NAME>
      # auth_env:            NEXUS_GETDESIGN_KEY   (never inline a secret here)
      # result_selector:     CSS selector, or `json:` + dotted path
      # content_field:       text | html | json
      # max_results:         10

    # - name: <your-site-2>
    #   channel: channels/<file>.py
    #   weight: 1.0
    #   use_cases: [web-dev]
    #   notes: >
    #     What it is good for, and what it is NOT good for.

  # ─────────────── AI / ML ───────────────
  ai-ml:
    - { name: arxiv,            weight: 1.1, use_cases: [ai-ml, research] }
    - { name: paperswithcode,   weight: 1.1, use_cases: [ai-ml] }
    - { name: semantic_scholar, weight: 1.0, use_cases: [ai-ml, research] }
    # - name: <your-ml-site>
    #   channel: channels/<file>.py
    #   weight: 1.2
    #   use_cases: [ai-ml]

  # ─────────────── RESEARCH ───────────────
  research:
    - { name: arxiv,          weight: 1.1, use_cases: [research] }
    - { name: openalex,       weight: 1.0, use_cases: [research] }
    - { name: crossref,       weight: 1.0, use_cases: [research] }
    - { name: pubmed,         weight: 1.0, use_cases: [research] }
    - { name: google_scholar, weight: 0.9, use_cases: [research] }   # rate-limit prone
    # - name: <your-paper-site>
    #   channel: channels/<file>.py
    #   weight: 1.2
    #   use_cases: [research]

  # ─────────────── DEVOPS ───────────────
  devops:
    - { name: github, weight: 1.1, use_cases: [devops, general-dev] }
    # - name: <your-devops-site>
    #   channel: channels/<file>.py
    #   weight: 1.2
    #   use_cases: [devops]

  # ─────────────── TRADING ───────────────
  trading:
    - { name: <your-trading-site>, channel: channels/<file>.py, weight: 1.4, use_cases: [trading] }
    # note: strategy-kb MCP already covers part of this; check before adding a site.

  # ─────────────── GENERAL DEV (fallback) ───────────────
  general-dev:
    - { name: github,           weight: 1.1, use_cases: [general-dev] }
    - { name: scrapling_fetch,  weight: 1.2, use_cases: [general-dev] }
    - { name: target_site,      weight: 1.0, use_cases: [general-dev] }
```

---

## 5. Filling in a site — worked template

For any site, answer these before writing the channel:

| question | why it matters |
|---|---|
| What is it **good for**? | Bind to the narrowest use case. A design site on `general-dev` pollutes everything. |
| What is it **bad for**? | Put it in `notes`. It stops a future-you from widening the binding. |
| Auth? | Use `auth_env`; **never inline a secret** in this file. |
| Rate limits? | Sets `timeout_seconds` and `weight` — a flaky site earns ~0.9, not 1.2. |
| Cacheable? | Static design refs → `cache_ttl: 3600`. Live prices → `0`. |
| Failure mode? | `fuse_registered()` isolates errors, so a dead site degrades and does not fail the query. |

### Channel skeleton

```python
# server/context_harness/channels/getdesign.py
from server.context_harness.models import HarnessResult

class GetDesignBackend:
    name = "getdesign"
    weight = 1.4
    cache_ttl = 3600.0
    timeout_seconds = 25.0

    async def is_available(self) -> bool:
        return True

    async def search(self, query: str, max_results: int = 10) -> list[HarnessResult]:
        # reuse the shared limiter — do not open a bare httpx client
        from server.context_harness.rate_limited_http import ...
        ...
        return [HarnessResult(source=self.name, content=text, score=0.8,
                              metadata={"url": url})]

    async def close(self) -> None:
        """Optional. If present, the registry disposes it (01-LLD.md §3.3)."""
```

`close()` is deliberately optional — `BackendRegistry.register()` uses `getattr`, so a site without it
still works and the 13 existing ones need no change.

---

## 6. Verification (per site, before you trust it)

1. Site appears in fused results for a use-case query and **not** for an unrelated one.
2. `is_available()` false → query still succeeds, just without that site.
3. Killing it mid-flight does not fail the query (`fuse_registered` error isolation).
4. With the site removed, results return to baseline (no residue).
5. `router_ab_suite` top-1 agreement non-regressing after binding a new site — a bad site can
   outrank a good one purely on weight.

---

## 7. Related tickets

- **`I5-1`** — disposable, extensible sites: adding a site = registry entry + one channel module, no
  `server.py` edit.
- **`I5-2`** *(new, implied by this doc)* — load `config/harness_sites.yaml` at startup and register
  sites programmatically. Until then this file is documentation and the code is the source of truth.
- **`C1-1`** — all 13 sites move to `BackendRegistry` with optional `close()`.
