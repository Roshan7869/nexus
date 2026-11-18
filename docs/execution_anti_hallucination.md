# Execution Design: Loop-Following Build with Anti-Hallucination

**Date:** 2026-09-14 · **Enforces:** `docs/nexus_docs_code_graph.md` (32 loops) via `docs/local_harness/README.md` order.
**Machine gate:** `scripts/verify_grounding.py` — **27/27 grounded, 0 FAIL, 0 WARN** (2026-09-14). Run it before every wave; a FAIL blocks the wave.
**Reuses (no reinvention):** `ToolAttentionV4.check_tool_call/batch_check_tool_calls` (`server/tool_attention_v4.py:668-719`, active-set membership + top-3 alternatives) · `hallucination_gate` (`tool_attention_v4.py:774`, `router.py:314`) · Archify `validate --quality showcase` (0/0 rule) · OpenResearch freeze + 2-round stop rule · `detect_changes()` pre-commit law.

---

## 1. Anti-hallucination contract (5 laws, binding on every loop)

1. **No ghost targets.** A MODIFY claim resolves to an existing path; a CREATE claim to an absent one. Enforced by `verify_grounding.py` (GHOST_TARGET / ALREADY_BUILT).
2. **No ghost anchors.** Every function/class/line a doc says it touches must `grep -F` in the target (GHOST_ANCHOR). Anchors are listed per doc in the verifier's `CASES`.
3. **No invented APIs.** New names appear first in the loop's doc pseudocode, then in code, then in tests — never in code first. Reviewer greps the doc for the name.
4. **Receipts over claims.** Each loop closes with a machine artifact: pytest output, `validate` JSON (0/0), `verify_grounding.py` line, or `detect_changes()` report. Prose alone never closes a loop.
5. **Stale-graph discipline.** GitNexus index is stale (KuzuDB v43 skew) — `impact()` outputs are advisory until re-index; `verify_grounding.py` + tests are authoritative meanwhile. Any `impact()`-based claim is labeled with the index date.

## 2. Hallucination taxonomy → gate response

| Hallucination | Example | Detecting gate | Response |
|---|---|---|---|
| Ghost target | doc edits `server/cache.py` (doesn't exist) | verifier GHOST_TARGET | halt wave; fix doc path |
| Ghost anchor | "extend `v5_fusion_scores`" but signature differs | verifier GHOST_ANCHOR + pre-edit `read` | re-read anchored region; amend doc |
| Invented API | code calls `ctx.effect()` before M3 defines it | review grep doc-first | move definition to doc, then code |
| Scope drift | M14 (docs-only) grows a behavior change | `detect_changes()` diff vs M-doc | split into new doc loop; revert |
| Stale-graph trust | `impact()` misses a caller (old index) | tests + `batch_check_tool_calls` on touched tools | tests authoritative; note index date |
| Unverified map edge | Archify IR edge with no graph source | `validate` + edge-source audit (B01 rule) | drop edge or cite EXTRACTED source |

## 3. Execution waves (loop order = build order)

**Wave gate (runs before every wave):** `python3 scripts/verify_grounding.py` → must print `0 FAIL`.

### Wave 0 — Pure-new close loops #1–4, #12, #18–19, #24–27 (zero existing-code risk)
- Loops: package init, context types, session, loader, redis-cache, 020 ledger, tests, harness.py, playbook.py, nexus_exp.py, 021 ledger.
- Per loop: open doc → write file per pseudocode → run doc's tests → `verify_grounding.py` (ALREADY_BUILT appears = progress) → commit `loop#N: path`.
- Wave receipt: `pytest tests/test_local_harness.py -v` green + verifier line.

### Wave 1 — IDE wiring loops #5–8, #16–17, #20, #28–29
- M5/M6/M7/M8 + config/shell + artifacts manifest (A07) + skill packs (A09, manual acceptance transcripts as receipt).
- Per MODIFY loop: pre-read anchored region → `impact()` (advisory, label index date) → minimal diff → module tests → `detect_changes()` compare vs M-doc scope.
- `NEXUS_LOCAL_HARNESS=0` compat proof per loop (flag-off == legacy behavior).

### Wave 2 — HIGH-risk loops #9–11 (gates active)
- M9/M10/M11. Full gate stack: live `impact()` + `detect_changes()` + A/B (`route_ab_compare`, 20-query suite) + shadow (`NEXUS_ROUTER_SHADOW`) + p95 check. Any CRITICAL without mitigation = halt (AGENTS.md law).
- Receipt: agreement delta + p95 + shadow report pasted in commit message body.

### Wave 3 — Hardening + proof loops #13–15, #30–31
- M13/M15 (+M14 docs-only) + Archify runtime map + flow IRs (B01/B03: `validate` 0/0 → `deliver` → freeze triple).
- Kill-FAISS drill (M9), insert→routable <2s (M4), chaos-100 (M3/M7) as wave receipts.

### Wave 4 — Learn + reconcile (loop engine turns over)
- `report_outcome` on every loop's skill (`correct` only with receipt attached — RC3 discipline).
- Graph JSON: flip closed loops `INFERRED→EXTRACTED`; re-run verifier (planned_missing shrinks = progress bar).
- Delta review (B2): first `archify compare` on the runtime map as the wave's own demonstration.

## 4. Per-loop execution card (copy per loop, fill IDs)

```
LOOP #__  doc: docs/local_harness/____.md  target: ____
[ ] WAVE-GATE  verify_grounding.py → 0 FAIL (paste line)
[ ] PRE-READ   read anchored region (MODIFY) / confirm absent (CREATE)
[ ] IMPACT     impact({target, upstream}) — advisory, index 2026-08-18 (HIGH rows only)
[ ] BUILD      follow doc pseudocode §verbatim; new names already in doc (Law 3)
[ ] VERIFY     doc's Tests section green (paste receipt)
[ ] ANTI-H     hallucination self-check: no ghost/invented/drift (taxonomy §2)
[ ] COMMIT     "loop#N: <path> — <receipt one-liner>"; detect_changes() clean vs doc scope
[ ] CLOSE      graph JSON edge → EXTRACTED (batch per wave, not per loop)
```

## 5. Tool-call grounding (runtime, not just build-time)

- Agent sessions driving the build run under `task_start` (active set ≤8) with `check_tool_call`/`batch_check_tool_calls` enforced after every model turn — a rejected call (`tool_not_available` + top-3 alternatives) is a hallucination caught live, logged, and retried against the active set only.
- Session freeze (A6/021) applies to build sessions too: a loop's session is immutable once its receipt lands; follow-ups = new sessions citing the receipt.

## Files
- `scripts/verify_grounding.py` — machine gate (27 cases; extend `CASES` with every new doc loop)
- `docs/local_harness/README.md` — loop index · `docs/nexus_docs_code_graph.md`/`.json` — loop map + closure state
- This doc — execution law + waves + per-loop card
