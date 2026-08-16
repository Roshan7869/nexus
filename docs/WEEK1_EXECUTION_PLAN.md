# NEXUS Week 1 Execution Plan — Pseudocode-Instructed (2026-08-17 → 2026-08-23)

Companion to `docs/EXECUTION_ROADMAP.md` (this executes Phase 0 + Phase 1, with a
TurboQuant spike and a dsh demo slice as weekend stretch goals).

## Reality check (measured 2026-08-16, before the week starts)

- `curl http://127.0.0.1:8000/health` → **dead** (daemon actually runs on 8080).
  The browser tab pointed at `:8000/v1` is staring at Phase 0's bug.
- Duplicate `nexus-mcp-enhanced.py` processes still running (PIDs 8141, 14691).
- Working tree still has the 23 dirty files. Days 1–2 are confirmed necessary.

## Research anchors used this week

| Anchor | Applied on |
|---|---|
| TurboQuant (arXiv 2504.19874, Google) — training-free 2–4 bit vector quantization via random Hadamard rotation + scalar quantization | Day 6 |
| DeepSeek Harness `dsh` (github.com/deepseek-ai/deepseek-harness) — model-agnostic plugin agent | Day 7 |
| DeepSeek V3.2 — verifier functions for auto-verified tasks; per-harness score reporting | Days 4, 7 |
| Harness engineering — golden datasets, scorer separation, computational sensors first | Days 3–5 |
| RouteLLM — ~100 curated golden labels prevent OOD router randomness | Day 4 |

## Harness-agent protocol (how swarms are used this week)

You (or ZCode) act as the orchestrating harness. Launch general-purpose subagents
for parallelizable authoring/benchmark work; keep runbook steps that touch live
processes (Day 2) for the human, out-of-session. Batch agents 2 at a time — the
environment rejects 5 concurrent spawns (observed 2026-08-16).

---

## Day 1 (Mon) — Freeze the tree

```
GOAL: clean, committed baseline. No process changes.

1.1 commit split (3 commits):
    git add server/broker_runtime.py server/nexus-mcp-enhanced.py \
            server/unified.py tests/test_unified.py <other server/*.py>
    git commit -m "feat(server): in-flight second-brain v2 changes"
    git add CLAUDE.md && git commit -m "docs: CLAUDE.md refresh"
    # NOTHING from logs/ or run/ gets committed — next step removes them from history's future

1.2 untrack runtime artifacts:
    git rm --cached db/nexus.db.bak db/backups/*.bak \
                 logs/ops_eval_ci.json logs/watcher.log run/supervisord.pid
    printf 'db/*.bak\ndb/backups/\nlogs/\nrun/\n*.db\nnexus.log\n' >> .gitignore
    git add .gitignore && git commit -m "chore: untrack runtime artifacts and DB backups (~95MB)"

EXIT: git status clean; git count-objects shows no new blobs from db/
```

## Day 2 (Tue) — One front door (OUT-OF-SESSION RUNBOOK for process steps)

```
GOAL: :8000 answers, exactly 1 MCP process, watcher explains its own exits.

2.1 port convergence (edit files first, in-session):
    supervisord.conf [program:nexus_http_daemon]:
        command=.../nexus_http_daemon.py --port 8000 --host 127.0.0.1
    grep -rn "8080" server/ config/ scripts/ | xargs -r review   # kill every straggler

2.2 watcher instrumentation (in-session):
    scripts/watch-inbox.sh:
        trap 'echo {ts:$(date -Iseconds),event:exit,code:$?} >> logs/watcher.jsonl' EXIT
        command -v inotifywait || { echo "FATAL: inotifywait missing"; exit 77; }
    # then read logs/supervisord.log for the historic daily-restart cause

2.3 PROCESS ROLLOVER — human executes out-of-session (guardrail: never kill live MCP
    from inside a working session):
    pkill -f "nexus-mcp-enhanced.py"        # both duplicates die
    venv/bin/supervisord -c supervisord.conf
    venv/bin/supervisorctl reread && venv/bin/supervisorctl update

2.4 smoke script:
    scripts/smoke_e2e.sh:
        assert curl -fsS :8000/health == 200
        resp = POST /v1/chat/completions {messages:[{role:user, "write haiku about routing"}]}
        assert resp.choices[0].message.content != ""
        assert POST /nexus/event {type:"code_accepted", model:resp.model} == 200
        stats = GET /nexus/learner/stats
        assert stats[resp.model].belief_changed_since(start_of_script)

EXIT: browser tab at :8000/v1 shows the API root; pgrep nexus-mcp-enhanced == 1;
smoke green 3 consecutive runs.
```

## Day 3 (Wed) — Eval harness skeleton (scorer before data)

```
GOAL: evals/ skeleton runs end-to-end on 5 seed cases.

3.1 schema (evals/golden/routing_cases.yaml):
    case = {id, prompt, expect_intent, accept_roles[role], cost_tier, tags[],
            verifier: {kind: role_check | exec_tests | judge, rubric?}}

3.2 runner (evals/run_eval.py) — lm-eval-harness pattern: declarative tasks,
    scorer separated from execution:
    def run(cases):
        for case in cases:
            d = route_for_task(case.prompt)          # direct call, offline, no provider
            yield score(case, d)                      # scorer.py: pure functions only
    score = {route_match: d.intent == c.expect_intent,
             role_ok: d.model.role in c.accept_roles,
             tier_ok: d.model.tier == c.cost_tier}

3.3 report: evals/results/run_<ts>.jsonl + markdown table; keep last baseline

EXIT: ./venv/bin/python evals/run_eval.py prints 5/5 scored in <5s.
```

## Day 4 (Thu) — Golden dataset by agent swarm + DeepSeek verifier functions

```
GOAL: ~100 golden cases, 20% held out; coding cases carry executable verifiers.

4.1 LAUNCH SWARM (2 agents at a time, batches):
    agent A: draft 25 coding/refactor cases (prompt + minimal file fixtures)
    agent B: draft 25 reasoning/math cases with checkable final answers
    agent C: draft 25 general/writing cases + rubrics
    agent D: draft 25 adversarial/OOD cases (ambiguous, multi-intent, junk)
    human: review + curate every case — quality over quantity (RouteLLM lesson)

4.2 verifier functions (DeepSeek V3.2 pattern — cheap ungameable scoring):
    for coding cases: verifier = {kind: exec_tests, tests: |
        def test_refactored(): assert behavior_preserved(sample_input)  |}
    runner: if verifier.kind == exec_tests and provider online:
        answer → extract_code() → sandbox_exec(tests) → pass/fail   # no LLM judge needed

4.3 split: evals/golden/holdout.yaml (20%) — NEVER tuned on, only reported.

4.4 logging fields (db.py, routing_log): add intent, route_match, latency_ms,
    cost, injected_ctx_len(=0 for now), harness_tag="raw-api".

EXIT: 100 cases load; offline scorer runs full set <30s.
```

## Day 5 (Fri) — Baseline, replay, CI gate → Phase 1 EXIT

```
GOAL: measured baseline + CI enforcement.

5.1 baseline: run_eval.py --tag baseline-2026-08-21 → archive
5.2 replay: evals/replay.py reads routing_log rows:
    for rec in routing_log: d = route_for_task(rec.prompt); compare(d, rec)
    # enables A/B of router changes without new traffic (used all of next week)
5.3 sampled judge: judge.py on 10% of golden, temperature=0, model snapshot pinned
    and recorded in the run file (model churn honesty)
5.4 CI: .github/workflows or scripts/ci.sh:
    step 1: pytest tests/ -q
    step 2: smoke_e2e.sh (skipped if no OLLAMA_CLOUD_API_KEY)
    step 3: run_eval.py offline — FAIL if route_match drops vs baseline

EXIT (= Phase 1 gate): one command scores any router <30s offline;
CI red/green meaningful; baseline + holdout results archived.
```

## Day 6 (Sat) — TurboQuant spike (Phase 4 pulled forward, benchmark only)

```
GOAL: know exactly what 4-bit vectors cost NEXUS — data for Phase 4 decision.

6.1 implement prototype (evals/spikes/turboquant.py) per arXiv 2504.19874:
    def encode(V, bits=4):
        R  = hadamard_like_rotation(dim=V.dim, seed=fixed)   # training-free
        Vr = V @ R                                            # rotate: spreads outliers
        s  = abs(Vr).max(axis=-1, keepdims=True)              # per-vector scale
        return clip(round(Vr / s * (2**(bits-1) - 1))), s.astype(fp16)
    def asymmetric_dist(qa, sa, b_fp):                        # re-rank vs originals
        return norm(dequant(qa, sa) - b_fp)

6.2 benchmark on real data (ruvector.db embeddings):
    for bits in [8, 4, 2]:
        Q = encode(V, bits)
        recall = |topk(V,10) ∩ topk(Q,10)| / 10
        report(bits, recall, bytes_per_vector)
    compare: float32 baseline (~dim*4 B/vec) vs TurboQuant (dim*bits/8 + 2 B/vec)

6.3 decision memo (docs/decisions/0001-turboquant.md):
    adopt-if: recall@10 >= 0.95 at 4 bits AND index memory < 40% of float32
    else: keep float32; revisit with the RaBitQ-style residual refinement variant

EXIT: memo with measured numbers on YOUR vectors, not the paper's.
```

## Day 7 (Sun) — dsh demo slice (Phase 5 pulled forward, demo only)

```
GOAL: an external agent runs through NEXUS end-to-end, and its traffic is tagged.

7.1 install: npx @deepseek-ai/dsh  (dev preview; MIT)
7.2 point dsh at NEXUS (it is model-agnostic — any OpenAI-compatible endpoint):
    dsh config: base_url=http://127.0.0.1:8000/v1, api_key=<any>, model=<routed role>
7.3 run ONE contained coding task in dsh inside a scratch git repo
7.4 verify the loop closed:
    routing_log rows with harness_tag="dsh" exist
    learner beliefs moved for the models dsh's requests hit
    (optional) dsh success/failure → POST /nexus/event as feedback
7.5 write per-harness reporting hook (DeepSeek V3.2 honesty pattern):
    run_eval.py --group-by harness_tag   # one column per client harness

EXIT: demo transcript + eval table split by harness (raw-api vs dsh).
STRETCH, NOT GATE: if dsh setup fights back, bank the eval table and stop.
```

---

## Week 1 exit summary

| Day | Deliverable | Gate |
|-----|-------------|------|
| 1 | clean tree, artifacts untracked | git status clean |
| 2 | :8000 live, 1 MCP, instrumented watcher, smoke script | smoke 3× green |
| 3 | evals/ skeleton | 5 cases scored <5s |
| 4 | 100 goldens + verifiers + holdout | full offline run <30s |
| 5 | baseline + replay + CI | Phase 1 gate |
| 6 | TurboQuant memo on real vectors | measured recall/bytes |
| 7 | dsh-through-NEXUS demo | harness-tagged traffic |

## If blocked

- Day 2 rollover goes sideways → rollback: `supervisorctl shutdown`, relaunch old
  commands directly; Day 3+ does not depend on the rollover succeeding.
- Ollama Cloud key missing → smoke step 2 degrades to assert-only; evals offline
  path is unaffected; judge runs deferred to Day 5 evening.
- Agent swarm spawns fail (concurrency) → batches of 2, retry; drafts are also
  fine handwritten if needed.
