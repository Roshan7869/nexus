# NEXUS — Execution: Harness Sites

Elaborates `05-SITES-REGISTRY.md`. Adding a site is three steps; this is each step at command level,
plus a worked end-to-end example using the `getdesign` slot you will fill.

Grammar: `exec/README.md` §1. Tickets: `I5-1` (disposable sites), `I5-2` (YAML loader).

---

## §0 — Verify the contract before writing anything

```
S0.1  the protocol exists
      grep -n "class BackendProtocol\|class BackendRegistry\|async def fuse_registered" \
        server/context_harness/backend_protocol.py
      ASSERT  BackendRegistry :35 · fuse_registered :70 · the policy line :142

S0.2  the four members + two methods
      sed -n '/class BackendProtocol/,/is_available/p' server/context_harness/backend_protocol.py
      ASSERT  name · weight · cache_ttl · timeout_seconds · async search() · async is_available()

S0.3  the OLD path is still the live one        (this is what I5-1 removes)
      grep -c "fuser.register(BackendDef" server/context_harness/server.py
      ASSERT  expect 13   (lines 918,943,968,991,1013,1032,1051,1070,1089,1113,1136,1160,1188)

S0.4  the class name is RRFFuser, NOT Fuser      <- P1
      grep -n "^class RRFFuser" server/context_harness/fusion.py
      ASSERT  expect 34

S0.5  fuse_registered's real behaviour
      sed -n '70,140p' server/context_harness/backend_protocol.py
      ASSERT  parallel execution · timeout · dedup · weight · error isolation ·
              RRF fusion with RRF_K = 60.0
```
**A site is consulted only for the use cases it is bound to.** Verify that binding mechanism exists
before assuming your site will be scoped — see S3.

---

## §1 — STEP ONE: the channel module

```
FILE    server/context_harness/channels/getdesign.py      (your site's name)
```

### S1.1 — Model on the live example
```
CHECK   grep -n "scrapling_fetch" server/context_harness/server.py | head -3
DO      read the scrapling_fetch registration at server.py:910-919 and its channel module
ASSERT  you can see the exact HarnessResult shape returned — source / content / score / metadata{url}
```
**Copy a live backend's shape. Do not invent one.** The 15 existing modules in
`server/context_harness/channels/` are the reference:
`arxiv_search · crossref_search · dispatcher · github_search · google_scholar · openalex_search ·
paperswithcode_search · pubmed_search · scrapecraft_pattern · scrapling_fetch · scrapling_spider ·
semantic_scholar · sherlock_probe · target_site`.

### S1.2 — Write it
```
DO      the skeleton from 05-SITES-REGISTRY.md §5:
          class GetDesignBackend: name/weight/cache_ttl/timeout_seconds
                                  async is_available()
                                  async search(query, max_results) -> list[HarnessResult]
                                  async close()  # optional
ASSERT  python -c "import ast;ast.parse(open('server/context_harness/channels/getdesign.py').read())"
        → exit 0
```

### S1.3 — Reuse the shared HTTP path
```
CHECK   ls server/context_harness/rate_limited_http.py
ASSERT  NO bare httpx client. A new site that opens its own client bypasses the rate limiter and
        the cache, and will be the one that gets the daemon IP-banned.
```

### S1.4 — `close()` is OPTIONAL by design
```
ASSERT  a site WITHOUT close() still works — BackendRegistry.register() dispatches via
        `getattr(backend, "aclose", None) or getattr(backend, "close", None)`
ASSERT  this is why the 13 existing backends need no change
```

---

## §2 — STEP TWO: the registry entry

```
FILE    config/harness_sites.yaml        (created alongside this doc)
```

### S2.1 — Confirm the loader status first
```
CHECK   grep -rn "harness_sites" --include=*.py server/ | grep -v venv
ASSERT  **expect 0 today** — the loader is ticket I5-2 and does NOT exist yet.
        Until I5-2 lands, harness_sites.yaml is DOCUMENTATION and must be mirrored by a real
        `BackendRegistry.register(...)` call. State this plainly; do not imply the YAML executes.
```

### S2.2 — Add the entry
```
DO      under `web-dev:` add the getdesign block (name / channel / weight / cache_ttl /
        timeout_seconds / use_cases / notes)
ASSERT  the file parses:
        ./venv/bin/python -c "import yaml;yaml.safe_load(open('config/harness_sites.yaml'))"
```

### S2.3 — Choose the weight against the LIVE anchor
```
ASSERT  scrapling_fetch = 1.2 · nexus/internet/memory = 1.0   (fusion.py:158-160)
RULE    a site more trusted than generic fetch  -> > 1.2
        a speculative / rate-limit-prone site    -> < 1.0   (google_scholar is 0.9 for this reason)
RULE    RRF uses RRF_K = 60.0, so weights scale RANK contributions, not raw scores —
        a large weight does not overwhelm a rank gap. Do not over-tune.
```

### S2.4 — NEVER inline a secret
```
ASSERT  auth is expressed as `auth_env: NEXUS_GETDESIGN_KEY`, and the value lives in the
        environment — not in this file, not in a commit.
CHECK   grep -rn "api[_-]\?key\|token\|secret" config/harness_sites.yaml
ASSERT  zero literal credentials
```

---

## §3 — STEP THREE: the use-case binding

```
S3.1  the use cases are the ProjectType enum
      sed -n '1,60p' server/context_harness/project_types.py
      ASSERT  AI_ML='ai-ml' · WEB_DEV='web-dev' · GENERAL_DEV='general-dev' ·
              RESEARCH · DEVOPS · TRADING
      ASSERT  WEB_DEV's existing keyword signature includes react/vue/nextjs/css/typescript/api

S3.2  BIND NARROWLY
      ASSERT  getdesign -> use_cases: [web-dev]  ONLY
      RULE    a design site bound to `general-dev` pollutes every query in the system.
              Bind to the narrowest use case that needs it.

S3.3  prove the scoping actually scopes
      ASSERT  a web-dev query returns the site; a backend_api query does NOT
```

---

## §4 — End-to-end acceptance (the whole point of I5-1)

Run all six. A site that passes S1-S3 but fails here is not integrated.

```
E1  APPEARS     the site shows in fused results for a web-dev query
E2  NOT ELSEWHERE  it does NOT appear for an unrelated use case
E3  DEGRADES    is_available() -> False ⇒ the query still SUCCEEDS, just without the site
E4  ISOLATED    killing the site mid-flight does NOT fail the query
                (fuse_registered's error isolation)
E5  NO RESIDUE  removing the site returns results to baseline
E6  NO REGRESSION  router_ab_suite top-1 agreement non-regressing after binding
```
**E6 is the one people skip.** A bad site can outrank a good one purely on weight; the A/B suite is the
only thing that catches it.

---

## §5 — `getdesign` worked example (your slot)

This is the concrete shape of the placeholder in `05-SITES-REGISTRY.md` and
`config/harness_sites.yaml`. Fill these in; I do not know your site.

```yaml
# config/harness_sites.yaml — under `web-dev:`
- name: getdesign
  channel: channels/getdesign.py
  weight: 1.4              # design-specific => trusted above generic fetch (1.2)
  cache_ttl: 3600          # static design refs => cache aggressively
  timeout_seconds: 25
  use_cases: [web-dev]     # NARROW — do not add general-dev
  notes: >
    Design references and layout patterns for frontend tasks.
    NOT for backend/API queries — that is why the binding is web-dev only.
  # --- FILL THESE IN ---
  # base_url:          <your site>
  # auth:              none | header:<NAME> | query:<NAME>
  # auth_env:          NEXUS_GETDESIGN_KEY
  # result_selector:   CSS selector, or `json:` + dotted path
  # content_field:     text | html | json
  # max_results:       10
```

### How to fill it (in order — do not guess)

```
F1  Open your site in a REAL browser session and capture ONE result's shape.
    Record: is it HTML or JSON? which selector/path? which field is the content?
F2  Write the channel to return exactly `HarnessResult(source, content, score, metadata{url})`.
F3  Set weight by comparing quality against scrapling_fetch on the SAME query.
    Better => >1.2. Comparable => 1.0. Flaky => <1.0.
F4  Set cache_ttl: static references 3600s; anything that changes per-session 0.
F5  Run §4 E1-E6.
F6  Add the second site the same way. One site per channel module.
```

---

## §6 — Ticket I5-2: make the YAML executable

```
FILE    a startup loader + config/harness_sites.yaml
```

```
I5-2.1  CHECK   grep -rn "harness_sites" --include=*.py server/      -> expect 0 today
I5-2.2  DO      load the YAML at startup; resolve `channels/<file>.py`
                (importlib by relative path); construct each backend class
I5-2.3  DO      register via `BackendRegistry.register()` applying weight / cache_ttl /
                timeout_seconds from the entry
I5-2.4  VERIFY  adding a site to the YAML + restarting makes it appear in fused results
                with NO code edit
I5-2.5  VERIFY  a MALFORMED entry fails LOUDLY at STARTUP — not silently at query time
        ASSERT  this is the difference between a config file and a trap
I5-2.6  VERIFY  removing an entry DISPOSES the site (not merely hides it)
I5-2.7  ROLLBACK  keep the code-path registrations as a fallback until the loader is proven
```

**Dependency:** I5-2 can land before I5-1, but the two together are what makes "adding a site is
configuration" true. Until I5-1 completes, the 13 registrations at `server.py:918-1188` remain the
live path and your YAML entry is not executing.

---

## §7 — Where this connects

```
I5-1  disposable, extensible sites   prereq C1-1  (registry with disposal)
I5-2  YAML loader                    independent
C1-1  the declared migration         backend_protocol.py:142 policy satisfied
A1-2  NEXUS_DAEMON_URL -> :8000      a site that calls the daemon needs this first
```

**Order for a new site today (before I5-2):** write the channel (S1) → add a
`BackendRegistry.register()` call AND the YAML entry (S2, S3) → run E1-E6 (§4).
**After I5-2:** the YAML entry alone is sufficient.
