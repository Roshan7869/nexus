# NEXUS Execution Roadmap — Pseudocode-Guided (v1, 2026-08-16)

Derived from: codebase audit (2026-08-16), live process inspection, research swarm
(DeepSeek Harness/dsh, harness-engineering literature, LLM-routing literature,
agent-memory literature), hardware audit (2-core i5-5300U, 16GB RAM, no GPU).

**Hard hardware rule:** no local LLM inference, ever. Local work = SQLite, FAISS,
numpy graph math, INT8 embeddings. All LLM calls go to Ollama Cloud; heavy/LLM work
runs in idle-time batches (Phase 4 exploits this deliberately).

**Through-line:** Phases 0–2 make NEXUS *functional*, 3–4 make it *smart*,
5 makes it *strategic*. No phase starts before the previous phase's exit gate passes.

---

## Phase 0 — Convergence (this week)

**Objective:** one front door, one process each, green smoke test.
**Exit gate:** `supervisorctl status` all RUNNING for 24h; `curl :8000/health` 200;
`pgrep -f nexus-mcp-enhanced` == 1 PID; working tree clean.

### 0.1 Commit the dirty tree
```
# Split the 23 modified files into logical commits:
commit A "fix(runtime): port convergence"     → supervisord.conf, start-nexus-mcp.sh, nexus_http_daemon args
commit B "feat(server): in-flight v2 changes" → server/*.py, tests/test_unified.py
commit C "docs: CLAUDE.md refresh"            → CLAUDE.md
# Never commit: logs/*, run/*, db/*.bak  (see 0.2)
```

### 0.2 Untrack runtime artifacts (~95MB)
```
git rm --cached db/nexus.db.bak db/backups/*.bak logs/ops_eval_ci.json logs/watcher.log run/supervisord.pid
append .gitignore:  db/*.bak, db/backups/, logs/, run/, *.db, nexus.log
commit "chore: untrack runtime artifacts and DB backups"
```

### 0.3 Port convergence (8000 everywhere)
```
# nexus.yaml:            port: 8000          (keep)
# supervisord.conf [program:nexus_http_daemon]:
#   command = venv/bin/python3 server/nexus_http_daemon.py --port 8000 --host 127.0.0.1
# start_nexus.py default port stays 8000. Nothing may hardcode 8080.
assert(curl("http://127.0.0.1:8000/health") == 200)
```

### 0.4 Process convergence (OUT-OF-SESSION runbook — never kill live MCP from inside a session)
```
pkill -f "nexus-mcp-enhanced.py"          # kills BOTH duplicates
venv/bin/supervisord -c supervisord.conf
venv/bin/supervisorctl reread && venv/bin/supervisorctl update
assert(count(pgrep -f nexus-mcp-enhanced) == 1)
```

### 0.5 Diagnose watcher restart loop
```
# Evidence: logs/watcher.log shows "Watcher started" on Aug 13,13,14,15,16 → daily crash-restart.
inspect logs/supervisord.log + child logs for watch-inbox.sh exit reasons
patch scripts/watch-inbox.sh:
    - log exit code + signal to logs/watcher.jsonl  {ts, event:"exit", code}
    - trap 'log_exit' EXIT
    - guard: if inotifywait missing → fail loudly, don't silent-loop
```

### 0.6 Smoke test (single command)
```
# scripts/smoke_e2e.sh — the prototype's definition of "works":
step 1: GET  /health                          → expect 200
step 2: POST /v1/chat/completions {simple coding prompt}  → expect non-empty completion
        (verifies OLLAMA_CLOUD_API_KEY is present in the daemon env)
step 3: POST /nexus/event {type:"code_accepted", model:<routed_model>} → expect 200
step 4: GET  /nexus/learner/stats             → assert belief for routed model MOVED
exit 0 only if all 4 pass; wire into CI later (Phase 1)
```

---

## Phase 1 — NEXUS Eval Harness (week 1–2)

**Objective:** a versioned, deterministic measurement rig. Every later change is an
experiment scored by this. Research basis: harness-engineering principles (golden
datasets, scorer/execution separation, cheap computational sensors on every run,
LLM-judge sampled only).

**New layout:**
```
evals/
  golden/routing_cases.yaml      # versioned golden dataset
  replay/recorded_traffic.jsonl  # sampled from routing_log
  run_eval.py                    # entrypoint: evals/run_eval.py [--baseline <run_id>]
  scorer.py                      # deterministic scoring only — no provider calls
  judge.py                       # LLM-judge, sampled subset only
  results/run_<ts>.jsonl         # every run archived
```

### 1.1 Golden dataset (~100 cases to start)
```yaml
# evals/golden/routing_cases.yaml — declarative, versioned artifacts
- id: coding-refactor-001
  prompt: "Refactor this function to reduce cyclomatic complexity: ..."
  expect_intent: coding
  accept_roles: [coding]          # deterministic check: routed model's role
  cost_tier: T2_or_T3
  tags: [coding, refactor]
  judge: {enabled: true, rubric: "code compiles conceptually, behavior preserved"}
- id: reasoning-math-004
  prompt: "..."
  expect_intent: reasoning
  accept_roles: [reasoning]
  ...
# Author 25 cases each: coding / reasoning / general / agentic / adversarial(OOD)
```

### 1.2 Offline scorer (no network, runs in seconds on this laptop)
```python
# evals/run_eval.py
def run(offline=True):
    results = []
    for case in load("evals/golden/routing_cases.yaml"):
        decision = route_for_task(case.prompt)          # server/router_cutover.py:245 — direct call
        results.append({
            "id": case.id,
            "route_match": decision.intent == case.expect_intent,        # computational sensor
            "role_ok":    decision.model.role in case.accepted_roles,
            "tier_ok":    decision.model.tier in case.cost_tier,
        })
    return report(results, baseline=last_run())          # delta vs previous run

def replay(recorded_traffic):                            # router A/B without new traffic
    for rec in recorded_traffic:
        decision = route_for_task(rec.prompt)
        yield compare(decision, rec.original_decision)
```

### 1.3 Sampled LLM-judge (expensive sensor — sample ~10%)
```python
# evals/judge.py
for case in sample(golden, p=0.10):
    answer = provider.chat(case.prompt, temperature=0)   # pinned snapshot + temp 0 = reproducible
    verdict = cloud_llm_judge(answer, case.judge.rubric) # 1..5
    record(case.id, verdict)
```

### 1.4 Full request logging (failures become golden cases)
```sql
-- extend routing_log (db.py): one row per decision
-- route_match, model, role, tier, latency_ms, cost, injected_ctx_len, judge_score
```

**Exit gate:** `evals/run_eval.py` scores any router in <30s offline; baseline run
archived; CI job runs offline scorer + smoke test on every commit.

---

## Phase 2 — Context Injection: close the harness→prompt loop (week 2–3)

**Objective:** the built-but-unwired retrieval stack actually feeds prompts.
Known gap: `/v1/chat/completions` (server/unified.py:173) forwards messages verbatim;
TOIN pipeline (engine.py:45-182) has zero non-test callers;
`get_cached_output` (engine.py:98) returns a FormattedEntry with empty text.

### 2.1 Fix the cache bug first (prerequisite, tiny)
```python
# server/context_harness/engine.py:98 — get_cached_output
# BUG: returns FormattedEntry(text="") → cache hits lose all content
def get_cached_output(key):
    entry = cache.get(key)
    return FormattedEntry(text=entry.formatted_text, ...)   # carry the text through
+ regression test: store → get → text non-empty
```

### 2.2 Injection hook (feature-flagged)
```python
# server/unified.py — handle_chat_completions (~line 173), before provider dispatch
if os.getenv("NEXUS_CONTEXT_INJECTION") == "true" and req.messages:
    query = last_user_text(req.messages)

    ctx = harness_engine.search(                     # ContextHarnessEngine (engine.py:24)
        query,
        backends = cfg.enabled_backends,             # start: nexus_bridge only; add web/memory later
        top_k     = cfg.ctx_top_k,                   # start 5
        budget_ms = cfg.ctx_budget_ms,               # hard cap 800ms — never block routing longer
    )
    block = toin.format_context(ctx.entries)         # wake up TOIN (engine.py:45-182)
    if block:                                        # empty retrieval → inject nothing
        req.messages = prepend_system_block(
            req.messages,
            f"<nexus_context relevance_sorted=true>\n{block[:cfg.ctx_max_chars]}\n</nexus_context>",
        )

    log(routing_log, injected_ctx_len=len(block), ctx_latency_ms=elapsed)   # Phase 1 sensor

resp = provider.send(req)                            # downstream UNCHANGED
```

```yaml
# nexus.yaml
context_injection:
  enabled: ${NEXUS_CONTEXT_INJECTION:-false}   # OFF by default; flip per-experiment
  top_k: 5
  budget_ms: 800
  max_chars: 4000        # same cap tool_session_broker._build_context_block uses
```

### 2.3 Measure (this is the whole point)
```
run evals with flag OFF → baseline
run evals with flag ON  → delta on judge_score subset (route_match must stay flat)
gate: merge only if judge_score improves and p95 latency overhead < budget_ms
```

**Exit gate:** A/B on the golden set shows measurable quality improvement from
injection; latency overhead within budget; TOIN path has non-test callers.

---

## Phase 3 — Router Hardening (week 3–4)

**Objective:** make the learning loop robust to the three researched pitfalls:
non-stationarity, feedback bias, cold start. Each item = one PR, one eval run.

### 3.1 Discounted Thompson Sampling (stale-belief fix — models change under you)
```python
# server/learning.py — belief sampling
GAMMA = 0.98          # per-day decay toward prior; tune via evals replay
def sample_quality(model, intent):
    a = GAMMA**model.days_since_update() * model.alpha + (1 - GAMMA**...) * ALPHA_PRIOR
    b = GAMMA**model.days_since_update() * model.beta  + (1 - GAMMA**...) * BETA_PRIOR
    return betavariate(a, b)
# validate: replay recorded_traffic → decayed posteriors must beat static on drift cases
```

### 3.2 Failure attribution (EWMA — Ramp pattern; don't learn from user errors)
```python
LAMBDA = 0.1
def on_provider_response(model, outcome):
    if outcome.is_provider_fault:        # 5xx / timeout / rate-limit ONLY
        model.failure_ewma = LAMBDA*1 + (1-LAMBDA)*model.failure_ewma
    elif outcome.is_user_fault:          # 4xx, bad request — attribute to nobody
        pass
    else:                                # success signal → existing beta update
        update_belief(model, success=outcome.accepted)

def route_score(model, intent):          # blended objective
    return (1 - model.failure_ewma) * sample_quality(model, intent)
```

### 3.3 Cascade fallback (FrugalGPT pattern)
```python
def route_with_cascade(req, max_escalations=2):
    ranked = thompson_ranked_models(intent=req.intent)     # full ranking, not just argmax
    for i, model in enumerate(ranked[:max_escalations+1]):
        try:    return provider.send(req, model)
        except ProviderFault:  log_escalation(i); continue
    return exhausted_error(ranked)                         # rare; alert
```

### 3.4 Local intent classification (semantic-router pattern — zero LLM calls)
```python
# share ONE INT8 bge-small ONNX embedder with the context harness (Phase 4 uses it too)
intent_centroids = {intent: mean_embedding(golden_cases_of(intent)) for intent in INTENTS}
def classify(prompt):
    emb = embedder.encode(prompt)                          # <10ms on this CPU
    return argmax(cosine(emb, intent_centroids))
# hybrid rule: if max cosine < CONF_FLOOR (0.35): fall back to existing classifier
# eval gate: intent accuracy on golden set must not regress vs current classifier
```

### 3.5 Cold-start priors
```python
# before first live traffic: run golden set through each candidate model (batch, one evening)
# seed alpha/beta per (intent, model) from those outcomes + optimistic prior alpha0=1, beta0=1
# RouteLLM lesson: ~100 curated labels already prevent most OOD randomness
```

**Exit gate:** offline eval shows routing accuracy ↑ and cost/request ↓ vs Phase 1
baseline; replay demonstrates decayed posteriors recover from simulated model drift.

---

## Phase 4 — Sleep-Time Second Brain (month 2)

**Objective:** research-validated memory: two-tier stores, bi-temporal facts,
nightly LLM consolidation (idle-time, latency-insensitive), HippoRAG-style PPR
retrieval over the existing louvain graph (pure numpy — GPU-free by design).

### 4.1 Bi-temporal migration (cheap, do first)
```sql
-- migrations/NN_bitemporal.sql
ALTER TABLE resources    ADD COLUMN valid_from TEXT;   -- world time
ALTER TABLE resources    ADD COLUMN valid_to   TEXT DEFAULT NULL;  -- NULL = current
ALTER TABLE resources    ADD COLUMN learned_at TEXT;   -- insertion/revision time
-- same for learnings, semantic summaries table (new: semantic_facts)
```

### 4.2 Nightly consolidation job (new supervised program)
```python
# server/maintenance/sleep_time.py — supervisord [program:nexus_sleep], cron-like 02:00
def nightly():
    for cluster in louvain_graph.clusters():
        if cluster.last_consolidated older_than 7d or cluster.size > threshold:
            docs = fetch_docs(cluster.member_ids)
            summary = cloud_llm(                      # STRONG/SLOW model — idle time, no latency budget
                prompt=reflect_prompt(docs),
                model=cfg.sleep_model,                # e.g. deepseek-v4-pro class
            )
            facts = extract_facts(summary)            # LLM extraction HERE, not per-ingest
            semantic_store.upsert(facts, valid_from=now(), learned_at=now())
            mark_consolidated(cluster)
    merge_duplicates(cosine_threshold=0.95)           # dedupe embeds
    compact_episodic()                                # archive raw logs older than N days
```
Research note: per-ingest LLM extraction is the cost nobody benchmarks — NEXUS
avoids it entirely; extraction happens only in this batch.

### 4.3 PPR retrieval expansion (query-time, pure CPU)
```python
def retrieve(query, k=10):
    seeds  = faiss.search(embed(query), k=8)                   # vector hit seeds the graph
    scores = personalized_pagerank(                            # numpy power iteration
        graph=louvain_graph, seeds=seeds,
        restart_prob=0.15, steps=20,
    )
    return top_k_nodes(scores, k) + semantic_facts.lookup(query)
# HippoRAG-2 result: beats vector-only by ~7% incl. associative tasks; costs no LLM calls
```

### 4.4 Embedding swap (INT8 bge-small, ONNX)
```
offline reindex: embed all resources with bge-small-en-v1.5 INT8 → rebuild FAISS
A/B via evals with a memory-probe golden subset (questions answerable only from local corpus)
rollback: keep old index until new one wins the probe
```

**Exit gate:** memory-probe eval: day-2 answers measurably better than day-1;
nightly job survives a week under supervisord; p95 retrieval < 200ms on this CPU.

---

## Phase 5 — Plugin Architecture & dsh Integration (month 2+)

**Objective:** align with the ecosystem direction DeepSeek just set (`dsh`,
"everything is a plugin", Aug 2026) — NEXUS becomes the self-learning routing +
memory layer behind other agents, not yet another agent.

### 5.1 Plugin interfaces (incremental refactor, no big-bang)
```python
class ProviderPlugin(Protocol):
    name: str
    def health(self) -> Health
    def chat(self, req) -> Response
    def list_models(self) -> list[Model]

class ChannelPlugin(Protocol):              # context_harness/channels/* conform to this
    name: str
    def search(self, query, top_k) -> list[Entry]

# registry.py — YAML-declared, entry_points-extensible (dsh pattern)
providers: registry.load(nexus.yaml.providers, entry_point="nexus.providers")
channels:  registry.load(nexus.yaml.channels,  entry_point="nexus.channels")
```

### 5.2 Serve real agents end-to-end
```
1. point an OpenAI-compatible client (dsh / Claude Code / OpenCode — README already
   targets these) at http://127.0.0.1:8000/v1 with any key
2. run a real coding task in the agent → NEXUS routes each call → learner updates
3. agent's own success/failure signals become feedback events (Area 3 attribution)
4. eval harness watches for drift as the agent's workload distribution shifts
```

### 5.3 Honest per-harness reporting (DeepSeek V3.2 pattern)
```
# every eval run records which client harness traffic came from
# report route_match/judge_score PER harness (dsh vs claude-code vs raw API)
# harness sensitivity is real — measure it, don't average it away
```

**Exit gate:** an external agent completes a real task entirely through NEXUS;
its feedback measurably moves learner beliefs; per-harness eval table published.

---

## Milestone summary

| Phase | Duration | Deliverable | Exit gate |
|-------|----------|-------------|-----------|
| 0 | this week | one front door | health 200, 1 MCP PID, clean tree |
| 1 | wk 1–2 | eval harness | offline score <30s, CI gate |
| 2 | wk 2–3 | context injection | A/B quality win, latency in budget |
| 3 | wk 3–4 | hardened router | accuracy↑ cost↓ on golden set |
| 4 | month 2 | sleep-time brain | day-2 > day-1 on memory probe |
| 5 | month 2+ | plugin + dsh | external agent learns through NEXUS |

## Standing risks

1. **Never kill the live MCP from inside a session** (existing guardrail) — Phase 0.4
   is an out-of-session runbook step.
2. **Ollama Cloud model churn** breaks pinned eval snapshots — record model versions
   in every eval run; expect Phase 3.1 to be load-bearing, not optional.
3. **Eval set overfitting** — hold out 20% of golden cases, never tune on them.
4. **This laptop is the bottleneck** — anything synchronous on the request path gets
   a hard budget_ms; everything LLM-heavy goes to nightly batches or the cloud.
5. **Scope creep via dormant subsystems** — the 163-file server/ stays dormant unless
   a phase explicitly wires it (Phase 2 wires TOIN; nothing else gets woken early).
