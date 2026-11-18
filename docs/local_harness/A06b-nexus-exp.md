# A06b — `scripts/nexus_exp.py` (CREATE, worktree experiment runner)

## GitNexus queries
```
query({search_query: "experiment run worktree eval sweep"}) · context({name: "BenchmarkRunner"})
```
Used: `third_party/OpenResearch/SKILL.md:19-48` (cardinal rules 1–4 + repair/refill/promote/stop), `src/plane/local_plane.rs:247-328` (launch/cancel/wait), `scripts/benchmark_nexus.py` + `run_ops_eval.py` (existing eval entry points), A06c-doc (021 ledger).

## Research-backed context
- **OpenResearch cardinal rules (primary law):** (1) freeze answered nodes incl. root — branch children for ideas; (2) run command + env FIXED contract, inherited verbatim — vary committed code only; (3) hyperparams in code/config branches, never CLI sweeps; (4) descend onto winners. Every run ends repair/refill/promote/stop.
- **Tencent HunyuanVideo/DiT scaling-law method:** fit envelopes (Nopt/Dopt) before committing compute — sweeps here size router tiers + FAISS budgets, not model weights.
- **NEXUS RC3** (feedback invents labels): frozen runs with recorded outcomes are the antidote to `routed`-as-signal — every sweep row carries a terminal outcome.

## Current state
No sweep runner. `benchmark_nexus.py`/`run_ops_eval.py` run single configs in-place (violates rule 2 when flags vary per run).

## Blast radius: NONE (new script, read-only vs live DB by default; `--apply` flag required to record)

## Detailed pseudocode
```python
#!/usr/bin/env python3
"""nexus_exp — git-worktree eval sweeps with frozen runs (cf. orx-git + cardinal rules)."""
# orx-exp run <id> analog: nexus_exp run --title <t> --cmd "<fixed>" --var key=val ...
FIXED Judges: route_ab_compare (agreement), ci_eval gates, p95 probe

def create(title, base="HEAD") -> str:  # → branch exp/<slug>
    run(["git", "worktree", "add", f"../runs/{slug}", "-b", f"exp/{slug}", base]); return slug
def run_fixed(slug, cmd) -> RunRecord:  # SAME cmd every node; vars live in committed files only
    ...subprocess in worktree... capture log/diff/summary...
def decide(record) -> "repair|refill|promote|stop":  # rule 4
    if not record.answered: return "repair"
    if record.beats_parent and budget_left: return "promote"   # descend: child becomes base
    if round_open: return "refill"
    return "stop"
def freeze(slug, outcome): ...write 021 row frozen=1...  # rule 1: never edit after
```

## Build steps
1. Implement create/run/freeze + `decide` (dry-run default; `--apply` writes ledger + worktrees).
2. First sweep: `NEXUS_V5_W_SHARED ∈ {0.0, 0.05, 0.10}` over 20-query suite, SAME command, three branches.
3. Record outcomes via `report_outcome` (feeds Thompson — closes RC3 for eval data).

## Tests
`test_fixed_command_inherited`, `test_frozen_never_rerun`, `test_decide_matrix`.
