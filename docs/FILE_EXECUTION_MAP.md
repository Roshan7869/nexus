# NEXUS File-by-File Execution Map — Week 1 continuous work

Machine-readable companion to `docs/WEEK1_EXECUTION_PLAN.md`. Every work item is a
**file card**: exact path, touch type, current context (with line anchors), change
instructions, verification, and dependencies. Any session (human, ZCode, or swarm
agent) should be able to open this file, find the next unchecked card, and execute
it without re-deriving context.

---

## Session protocol (READ FIRST, every session)

```
1. read order:   docs/EXECUTION_ROADMAP.md (phases)
                 docs/WEEK1_EXECUTION_PLAN.md  (this week)
                 docs/FILE_EXECUTION_MAP.md    (this file — pick next card)
2. git status    — if dirty from a previous session, finish/commit that card first
3. pick the FIRST unchecked card in ledger order; cards within a day are
   dependency-ordered — never reorder
4. execute card: MODIFY cards → read the anchored region first, apply change,
   run the card's VERIFY line
5. tick the ledger checkbox; if blocked, tick [~] and write one line in BLOCKERS
6. commit with the card id in the message:  "w1d3-4: evals/scorer.py"
```

Guardrails carried over:
- GitNexus MCP tools (`impact`/`detect_changes`) are unavailable in this environment.
  Manual protocol instead: for every MODIFY card, run the card's VERIFY and the
  module's existing tests before commit (`./venv/bin/python -m pytest tests/<related> -q`).
- NEVER kill the live MCP from inside a session (supervisord.conf guardrail).
  Cards marked OUT-OF-SESSION are human runbook steps.

## STATUS LEDGER

```
Day 1  [x] 1.1  .gitignore untrack rules
       [x] 1.2  (git ops — commits done)
Day 2  [x] 2.1  port convergence (systemd unit + daemon default + supervisord.conf)
       [x] 2.2  scripts/watch-inbox.sh instrumentation
       [~] 2.3  OUT-OF-SESSION runbook — AMENDED, see below (not yet executed)
       [~] 2.4  scripts/smoke_e2e.sh — written+validated; full green BLOCKED on API key
Day 3  [x] 3.1  evals/ package skeleton
       [x] 3.2  evals/golden/routing_cases.yaml (seed 5)
       [x] 3.3  evals/scorer.py (+ tests/test_eval_scorer.py — 8 pass)
       [x] 3.4  evals/run_eval.py — offline, 5 cases in ~31s; first signal:
                pass_rate 0.6, skill_match 0.5, cluster_ok 0.75
                (frontend + qa-security seeds miss; embedder reloads per call —
                candidate optimization later, not a blocker)
Day 4  [~] 4.1  golden set — 100 drafts DONE by swarm (evals/golden/drafts/
            {coding,reasoning,general,ood}.yaml), validated (unique ids, all
            expect_skills/accept_clusters exist in DB); HUMAN CURATION PENDING —
            after curation merge into routing_cases.yaml, then delete drafts/
       [ ] 4.2  evals/golden/holdout.yaml split (after curation)
       [ ] 4.3  evals/verifiers/ fixtures + verifier_runner
       [x] 4.4  server/db.py routing_log columns (commit 64f2d7b; verified on
            DB copy + test_unified 38/38)
Day 5  [ ] 5.1  evals/replay.py
       [ ] 5.2  evals/judge.py
       [ ] 5.3  scripts/ci.sh (+ optional workflow)
       [ ] 5.4  baseline archive run
Day 6  [ ] 6.1  evals/spikes/turboquant.py
       [ ] 6.2  docs/decisions/0001-turboquant.md
Day 7  [ ] 7.1  dsh env wiring (external, no repo card)
       [ ] 7.2  run_eval.py --group-by harness_tag
       [ ] 7.3  docs/demo/dsh-demo.md

BLOCKERS (2026-08-16 session):
- OLLAMA_CLOUD_API_KEY in .env is STALE — direct curl to ollama.com/v1 returns
  401 with it. USER ACTION: refresh key. Until then smoke step 2 cannot go green.
- GATEWAY NOT SERVING IN PRODUCTION: systemd unit runs nexus_http_daemon.py
  (ops plane: /find_skills, /route, sessions — NO /v1/chat/completions). The
  README gateway (server/unified.py) is only launchable via start_nexus.py.
  DECISION NEEDED (runbook): cutover systemd unit to unified.py on :8000, or
  keep ops daemon on :8000 and serve gateway elsewhere. unified.py booted fine
  on a test port (:8010, ~4s startup, /health 200).
- Event→beliefs loop: event POST needs >10s on first call (embedder load);
  smoke now allows 60s. Unproven end-to-end until API key fixed.

POST-WEEK-1 WORK (2026-08-17):
- [x] U1a project continuity CORE: server/project_continuity.py — attach/
      resume/note, DB substrate + .nexus/memory.md + AGENTS.md excerpt
      (10/10 tests; cross-IDE demo proven; MCP tools project_attach/
      project_resume/project_note registered after find_skills)
- [x] default-project learnings leak fixed (tool_session_broker exact-match;
      6/6 broker tests)

═══ PIVOT 2026-08-17 (user direction) ═══════════════════════════════════════
GATEWAY/LLM-ROUTING REMOVED FROM SCOPE. NEXUS = cluster-team orchestrator:
project brief → forge plans lead + cluster peers → parallel OpenCode TUI tmux
sessions with territory briefs → continuity carries the memory.
- OLLAMA_CLOUD_API_KEY blocker: CLOSED-BY-PIVOT (no LLM proxy in scope).
- scripts/smoke_e2e.sh: gateway-era (steps 2-4 need unified gateway) — kept
  for a future return, not part of forge gates. De-loop Stage 0/1 OBSOLETE.
- [x] FORGE SHIPPED: server/forge.py (plan/spawn/status/stop; two-phase
      spawn with boot-wait, verify-and-retry pointer delivery, remain-on-exit
      panes, tmux list-sessions seat discovery), 4 MCP tools (forge_plan/
      spawn/status/stop), ide_manager.launch_tmux cwd param, 11/11 tests.
      Live demo 2026-08-17: 5 seats for "build an ecommerce site" — all
      spawned, pointers verified in panes, seats processing with 2 MCP
      servers connected. Retrieval direction (OKF sections + quantized
      vectors replacing FAISS hot paths): see EXECUTION_ROADMAP addendum.
════════════════════════════════════════════════════════════════════════════

- [ ] U1b: make IDE sessions call project_attach on connect (opencode.json
      instructions + CLAUDE.md nudge)
- [ ] Week-2: repo-map injection (tree-sitter decision pending) behind the
      assemble() seam (architecture candidate #1)
```

### Runbook 2.3 (AMENDED 2026-08-16 — replaces the original pkill-based card)
```
Reality found during execution: process topology is DELIBERATE split-ownership
(docs/process_topology.md, gap2.v1) — systemd owns the HTTP daemon, IDEs own
MCP stdio (the "two MCP processes" are two IDE sessions — NOT duplicates to kill).
Do NOT pkill the MCP processes.

1. systemctl --user daemon-reload                     # unit file edited: --port 8000
2. systemctl --user restart nexus-daemon              # ops daemon now on :8000
3. (decision) cutover to unified.py gateway, e.g.:
     Edit ~/.config/systemd/user/nexus-daemon.service:
       ExecStart=.../venv/bin/python3 -m uvicorn server.unified:app --host 127.0.0.1 --port 8000
       + EnvironmentFile=/home/roshan/nexus/.env      # gateway needs the API key
   OR run gateway as a second unit on :8000 after moving the ops daemon to :8010.
   IDEs currently using http://127.0.0.1:8080/{find_skills,route,...} must be
   re-pointed before the ops daemon moves — check .opencode/ and .mcp.json configs.
4. verify: NEXUS_BASE=http://127.0.0.1:8000 ./scripts/smoke_e2e.sh   (needs valid key)
```

---

# DAY 1 — freeze the tree

## Card 1.1 — MODIFY `.gitignore`
```
CONTEXT: exists; previously appended rules for large DBs (commit ee9c094) but
         runtime paths still tracked.
CHANGE:  append exactly (skip duplicates already present):
    db/*.bak
    db/backups/
    logs/
    run/
    *.db
    nexus.log
VERIFY:  git status --short | grep -cE "logs/|run/supervisord" → after 1.2 git-ops, 0
DEPENDS: none
```

## Card 1.2 — GIT OPS (no file)
```
A) git rm --cached db/nexus.db.bak db/backups/*.bak \
        logs/ops_eval_ci.json logs/watcher.log run/supervisord.pid
B) commit A "chore: untrack runtime artifacts and DB backups"   (.gitignore + removals)
C) commit B "feat(server): in-flight second-brain v2 changes"   (all server/*.py + tests/)
D) commit C "docs: CLAUDE.md refresh + roadmap docs"            (CLAUDE.md, docs/*.md)
VERIFY:  git status clean && git log --oneline -3
DEPENDS: 1.1
```

---

# DAY 2 — one front door

## Card 2.1 — MODIFY `supervisord.conf`
```
CONTEXT: [program:nexus_http_daemon] command uses --port 8080 (live PID 2296
         proves it); nexus.yaml says 8000; README says 8000.
CHANGE:  --port 8080 → --port 8000 in the nexus_http_daemon command line.
         grep the whole file for other 8080 references.
VERIFY:  grep -c "8080" supervisord.conf → 0
DEPENDS: none (but takes effect only after 2.3 rollover)
```

## Card 2.2 — MODIFY `scripts/watch-inbox.sh` (19 lines)
```
CONTEXT: bash + inotifywait loop; logs only "Watcher started" to logs/watcher.log;
         dies silently ~daily (Aug 13-16 evidence).
CHANGE (keep it small):
    #!/usr/bin/env bash
    set -u
    LOG_JSONL=/home/roshan/nexus/logs/watcher.jsonl
    trap 'echo "{\"ts\":\"$(date -Iseconds)\",\"event\":\"exit\",\"code\":$?}" >> "$LOG_JSONL"' EXIT
    command -v inotifywait >/dev/null || { echo "FATAL: inotifywait missing" >&2; exit 77; }
    ...existing loop...
    # plus: log each handled file as {ts, event:"ingested", file} to the jsonl
VERIFY:  bash -n scripts/watch-inbox.sh && ./scripts/watch-inbox.sh & sleep 1; kill %1
         → logs/watcher.jsonl contains an exit row
DEPENDS: none
```

## Card 2.3 — OUT-OF-SESSION RUNBOOK (human; no file card)
```
pkill -f "nexus-mcp-enhanced.py"     # both duplicates (8141, 14691 as of 2026-08-16)
venv/bin/supervisord -c supervisord.conf
venv/bin/supervisorctl reread && venv/bin/supervisorctl update
acceptance: pgrep -f nexus-mcp-enhanced → exactly 1 PID; curl :8000/health → 200
rollback: venv/bin/supervisorctl shutdown; relaunch old commands directly
```

## Card 2.4 — CREATE `scripts/smoke_e2e.sh`
```
PURPOSE: the prototype's executable definition (4 steps).
SKELETON:
    #!/usr/bin/env bash
    set -euo pipefail
    BASE=http://127.0.0.1:8000
    ts_start=$(date -Iseconds)
    code=$(curl -fsS -o /dev/null -w '%{http_code}' $BASE/health)          # 1 health
    resp=$(curl -fsS -X POST $BASE/v1/chat/completions -H 'Content-Type: application/json' \
           -d '{"messages":[{"role":"user","content":"write a haiku about routing"}]}')
    model=$(echo "$resp" | ./venv/bin/python -c 'import sys,json;print(json.load(sys.stdin)["model"])')
    content=$(echo "$resp" | ./venv/bin/python -c 'import sys,json;print(json.load(sys.stdin)["choices"][0]["message"]["content"])')
    [ -n "$content" ]                                                      # 2 real completion
    curl -fsS -X POST $BASE/nexus/event -d "{\"type\":\"code_accepted\",\"model\":\"$model\"}" >/dev/null   # 3 feedback
    stats=$(curl -fsS $BASE/nexus/learner/stats)                           # 4 beliefs moved
    echo "$stats" | grep -q "$model"
    echo "SMOKE GREEN ($model)"
NOTES: adapt field names to the actual API once (unified.py:173 defines the
       request schema; /nexus/event + /nexus/learner/stats exist per README).
VERIFY: run 3× consecutively green (requires 2.3 done + OLLAMA_CLOUD_API_KEY set)
DEPENDS: 2.1, 2.3
```

---

# DAY 3 — eval harness skeleton

## Card 3.1 — CREATE `evals/__init__.py`, `evals/golden/`, `evals/results/.gitkeep`
```
empty package; results/ is gitignored EXCEPT .gitkeep? NO — keep results/ tracked
(empty dir marker only); run outputs go to evals/results/ and ARE committed
(baselines must be reproducible-traceable).
VERIFY: ./venv/bin/python -c "import evals"  (from repo root)
DEPENDS: none
```

## Card 3.2 — CREATE `evals/golden/routing_cases.yaml` (seed 5)
```
SCHEMA (frozen today — Day 4 only adds instances, never fields):
    - id: string-unique
      prompt: string
      expect_intent: coding|reasoning|general|agentic|vision|long_context
      accept_roles: [role,...]        # model.role allowed (see nexus.yaml roles)
      cost_tier: T1|T2|T3|T2_or_T3
      tags: [free-form]
      verifier: {kind: role_check|exec_tests|judge, rubric: string?}
SEED 5: one coding, one reasoning, one general, one adversarial/ambiguous,
        one long-context.
DEPENDS: 3.1
```

## Card 3.3 — CREATE `evals/scorer.py` (pure functions, no I/O)
```
def score_case(case: dict, decision) -> dict:
    return {
      "id":          case["id"],
      "route_match": decision.intent == case["expect_intent"],
      "role_ok":     decision.model.role in case["accept_roles"],
      "tier_ok":     getattr(decision.model, "tier", None) == case["cost_tier"]
                     or case["cost_tier"].endswith("_or_" + str(getattr(decision.model,"tier",""))),
    }
def summarize(results: list[dict]) -> dict:   # counts + pass rates, no printing
RULES: no imports of server/, no network, no DB. Fully unit-testable.
VERIFY: pytest -q evals/ (add tests/test_eval_scorer.py — 3 tiny cases)
DEPENDS: 3.2
```

## Card 3.4 — CREATE `evals/run_eval.py`
```
CONTEXT: the decision source is server/router_cutover.py:245 route_for_task —
         the SAME function the MCP /find_skills and HTTP /route paths use
         (router_cutover centralizes dispatch). Offline = import and call it.
SKELETON:
    import sys, pathlib, yaml, argparse, json, datetime
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from server.router_cutover import route_for_task
    from evals.scorer import score_case, summarize
    def run(golden_path, tag, group_by=None):
        cases = yaml.safe_load(open(golden_path))
        out = []
        for case in cases:
            d = route_for_task(case["prompt"])          # offline; no provider call
            out.append(score_case(case, d))
        run_meta = {"tag": tag, "ts": datetime.datetime.now().isoformat(),
                    "cases": len(out), **summarize(out)}
        results_dir.write(run_id + ".jsonl")            # one line per case + meta line
        print_table(out, group_by)                      # group_by harness_tag later (7.2)
CAUTION: route_for_task attaches harness plan (router_cutover.py:271-284) —
         plan-only unless NEXUS_HARNESS_EAGER; confirm no network in plan path;
         if it touches channels, set env to disable (check before trusting "offline").
VERIFY: ./venv/bin/python evals/run_eval.py → 5/5 rows, <5s
DEPENDS: 3.2, 3.3
```

---

# DAY 4 — golden set + verifiers + logging

## Card 4.1 — MODIFY `evals/golden/routing_cases.yaml` → ~100 cases
```
SWARM PROTOCOL (batches of 2): agents draft per intent —
    A: 25 coding/refactor (with minimal code fences in prompt)
    B: 25 reasoning/math (final answer checkable)
    C: 25 general/writing + rubrics
    D: 25 adversarial: ambiguous, multi-intent, junk, OOD
HUMAN: curate all — reject/fix anything you wouldn't defend in a demo.
DEPENDS: 3.4 (schema proven)
```

## Card 4.2 — CREATE `evals/golden/holdout.yaml`
```
random.sample(cases, 20%) moved OUT of routing_cases.yaml. Holdout is REPORTED
only — any tuning against it is a protocol violation (write this warning in the
file header comment).
DEPENDS: 4.1
```

## Card 4.3 — CREATE `evals/verifiers/` + `evals/verifier_runner.py`
```
DeepSeek V3.2 pattern: auto-verifiable tasks.
    evals/verifiers/<case_id>.py     each exports:  def verify(answer_text) -> bool
    verifier_runner.py:
        def run_verifier(case, answer):
            if case.verifier.kind != "exec_tests": return None
            mod = importlib.import_module(f"evals.verifiers.{case.id}")
            code = extract_code_block(answer)          # first ``` fence
            return bool(mod.verify(code))              # subprocess with timeout=10
    SAFETY: verifiers run in subprocess with timeout; never in-process.
DEPENDS: 4.1 (coding cases exist)
```

## Card 4.4 — MODIFY `server/db.py` `ensure_routing_log_schema` (line 163)
```
CONTEXT: db.py already migrates routing_log in-code via routing_log_new copy
         pattern (lines 172-196). EXTEND that pattern — do not invent a new one.
CHANGE: add columns to the new-table definition:
    intent           TEXT,        -- classified intent
    route_match      INTEGER,     -- 1/0
    latency_ms       REAL,
    cost             REAL,
    injected_ctx_len INTEGER DEFAULT 0,   -- Phase 2 sensor lands here
    harness_tag      TEXT DEFAULT 'raw-api'
Same INSERT-preserving migration shape as existing code. Bump any schema-version
marker the function uses. Do NOT touch callers today — writers start in Phase 2/3.
VERIFY: ./venv/bin/python -m pytest tests/test_unified.py -q  (38 pass — schema
        change must not break existing suites) + manual: open db, PRAGMA table_info
DEPENDS: none (independent of 4.1-4.3)
```

---

# DAY 5 — baseline, replay, CI

## Card 5.1 — CREATE `evals/replay.py`
```
reads routing_log rows (needs 4.4 columns where present):
    for rec in db.routing_log(limit=N):
        d = route_for_task(rec.prompt)
        yield compare(d, rec.intent)          # agreement matrix old-vs-new router
PURPOSE: A/B router changes without new traffic. No provider calls.
DEPENDS: 4.4, 3.4
```

## Card 5.2 — CREATE `evals/judge.py`
```
    for case in sample(golden, p=0.10, seed=fixed):
        answer = provider.chat(case.prompt, temperature=0)      # OLLAMA_CLOUD
        verdict = cloud_llm_judge(answer, case.verifier.rubric, score_1_to_5)
        record(case.id, verdict, model_snapshot=answer.model_version)
RULES: temperature=0; record model snapshot id; judge model differs from
       candidate model when possible.
DEPENDS: 4.1
```

## Card 5.3 — CREATE `scripts/ci.sh` (+ optional `.github/workflows/ci.yml`)
```
    #!/usr/bin/env bash
    set -euo pipefail
    ./venv/bin/python -m pytest tests/ -q
    ./venv/bin/python evals/run_eval.py --tag ci --fail-if-below baseline   # route_match
    [ -n "${OLLAMA_CLOUD_API_KEY:-}" ] && ./scripts/smoke_e2e.sh || echo "skip smoke"
VERIFY: ./scripts/ci.sh green twice
DEPENDS: 3.4, 2.4; --fail-if-below needs 5.4 baseline
```

## Card 5.4 — RUN baseline
```
./venv/bin/python evals/run_eval.py --tag baseline-2026-08-21
commit results file. PHASE 1 EXIT GATE: score any router <30s; CI meaningful.
DEPENDS: 4.1, 4.2, 5.3
```

---

# DAY 6 — TurboQuant spike

## Card 6.1 — CREATE `evals/spikes/turboquant.py`
```
INPUT: embeddings from ruvector.db (load via server db helpers or direct sqlite).
ALGO (arXiv 2504.19874, training-free):
    def encode(V, bits):
        R  = hadamard_rotation(V.shape[1], seed=42)     # scipy.linalg.hadamard padded
        Vr = V @ R
        s  = np.abs(Vr).max(axis=1, keepdims=True)
        return np.clip(np.rint(Vr / s * (2**(bits-1) - 1)), -(2**(bits-1)-1), 2**(bits-1)-1), s
    def recall_at_k(V, Q, s, k=10):                     # top-k overlap vs float32
        ...
    SWEEP bits ∈ {8,4,2}; print table: bits | recall@10 | bytes/vector | total MB
DEPENDS: none (isolated)
```

## Card 6.2 — CREATE `docs/decisions/0001-turboquant.md`
```
ADOPT-IF: recall@10 ≥ 0.95 at 4 bits AND memory < 40% of float32
ELSE:     keep float32; note RaBitQ residual-refinement variant as future work
DEPENDS: 6.1
```

---

# DAY 7 — dsh demo slice

## Card 7.1 — EXTERNAL ENV (no repo file)
```
npx @deepseek-ai/dsh → configure base_url=http://127.0.0.1:8000/v1 (any key),
run ONE contained coding task in a scratch git repo.
```

## Card 7.2 — MODIFY `evals/run_eval.py`
```
add --group-by harness_tag: results split per client harness (raw-api | dsh).
Needs traffic actually tagged — wiring dsh traffic through /nexus/event into
routing_log (4.4 column) is part of this card; if the gateway can't see the tag,
tag by API key per client (add clients table later — do NOT over-build).
DEPENDS: 7.1, 4.4
```

## Card 7.3 — CREATE `docs/demo/dsh-demo-2026-08-23.md`
```
transcript excerpt + per-harness eval table + learner-stats diff (before/after).
DEPENDS: 7.1, 7.2
```

---

## Cross-card invariants (checked at every commit)

1. `./venv/bin/python -m pytest tests/ -q` — no regressions (baseline: 38 pass in test_unified.py).
2. Offline means offline: `run_eval.py` must never touch network/providers — assert in CI with a socket guard if cheap.
3. Schema changes only via the `ensure_routing_log_schema` pattern (db.py:163).
4. Every new file lands with its ledger checkbox ticked in the same commit.
5. Holdout (4.2) is never tuned on.
