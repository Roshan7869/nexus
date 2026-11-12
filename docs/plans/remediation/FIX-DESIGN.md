# Fix Design — every audited issue, in triage order

Date: 2026-11-10 · Branch `second-brain-v2` · Companion documents:
- `docs/audits/triage-remediation-2026-11-10.md` — ranking, verdicts, live/latent/dead (the source of the order below)
- `docs/audits/file-audit-2026-11-10.md`, `docs/audits/execution-audit-2026-11-10.md` — the findings
- `docs/plans/remediation/EXECUTION.md` — the S-steps (S0 executed); this document references them and does not redefine them
- `docs/audits/research-findings-2026-11-10.md` — primary-source basis for the design choices marked **[research]**

Naming: `FA-*` = file audit, `B*` = security audit, `T*` = track added by triage, `S*` = EXECUTION.md step.

## 0. Operating rules (apply to every item)

1. **One fix, one commit, one rollback point.** Gate before the next commit.
2. **A fix is "done" only when its gate runs.** Items whose verification needs an IDE-owned MCP process are recorded as **pending owner restart**, never as passing (EXECUTION.md §3.1).
3. **Restart the daemon after every `server/*.py` edit:** `systemctl --user restart nexus-daemon && curl -s localhost:8000/health`.
4. **The suite must read 490 passed / 24 skipped**, forward and reverse order (`find tests -name 'test_*.py' | sort -r`). Measured cost 89 s.
5. **Before a dead-code deletion:** enumerate entry points (`spec_from_file_location`, `importlib.import_module` strings, `entry_points.txt`, `pyproject.toml`, `supervisord.conf`, `.mcp.json`, systemd units). Name-based importer counts are not evidence (research §6).
6. **Invariant for every label-writing path [research: AU-3, AU-9]:** a component writes a terminal outcome only if it observed the outcome. Otherwise it writes a non-terminal state (`routed`, `unverified`, `dispatched`). Partial and unknown are states, never scores.
7. **Fail closed, not open [research: NIST 800-53 CM-6, ASVS V16.5.3].** On an error in a safety check, refuse. Do not degrade to a permissive default.

---

## Track T0 — stop the live damage (first, before the suite runs again)

These write to the live DB or fail open today. They are small and have no decision dependency except T0-3.

### T0-1 · FA-D1 `/tmp` database fallback — refuse, do not copy
- **Verified:** `server/db.py:69-76` copies an unwritable DB to a predictable `/tmp/nexus.db` and uses it if it already exists; the copy's exception is swallowed.
- **Design:** the process either opens the configured DB or raises a `RuntimeError` naming the path and the permission problem. No copy, no `/tmp` path, no silent reuse. Read-only callers get a read-only URI (`mode=ro`) instead of a copy.
- **Steps:** (1) `grep -rn "/tmp/nexus.db\|nexus.db" server core scripts` to confirm no caller depends on the fallback. (2) Replace the branch in `resolve_db_path` with the raise. (3) Add a test.
- **Test:** `tests/test_db_refuses_unwritable.py` — monkeypatch the path to an unwritable directory; assert `RuntimeError`; assert no file appears under `tmp_path` or `/tmp/nexus.db`.
- **Gate:** suite 490/24; `ls -la /tmp/nexus.db` absent after the test run.
- **Rollback:** revert. No data change.

### T0-2 · FA-A3b sweeper hard-codes `"correct"`
- **Verified:** `server/broker_runtime.py:1072-1090`, `outcome="correct"`, `outcome_source="timeout"`. A terminal label on an unobserved result. This is the belief-corrupting half of FA-A3.
- **Design:** the timeout sweeper writes a non-terminal label. Use `unverified` (research: "unknown/unverified" vocabulary). `learning.py`'s `_normalize_outcome_label` already routes non-terminal labels to telemetry without touching counters, so the sweeper keeps its audit row and loses its belief update.
- **Steps:** change the literal; add the test; leave the rest of the sweeper alone (S1 unifies writers later).
- **Test:** `tests/test_sweeper_no_fabricated_correct.py` — a stale `tool_traces` row through `_close_routing_outcomes` on an in-memory DB (patch `learning._get_conn` and `broker_runtime._get_conn`, per S0); assert `resources.times_correct` unchanged and the row's outcome is `unverified`.
- **Gate:** the new test; suite 490/24.
- **Rollback:** revert.

### T0-3 · FA-A3c `auto_execute` defaults to `True`
- **Verified:** `server/broker_runtime.py:555` (`auto_execute: bool = True`), `server/nexus-mcp-enhanced.py:806`, `server/unified.py:156`. Only two callers of `create_task_run` (`nexus-mcp-enhanced.py:809`, `unified.py:457`).
- **Design [research: NIST CM-6; ASVS V16.5.3]:** deny by default. `auto_execute` defaults to `False` at all three sites. Execution requires an explicit opt-in per call, and the opt-in is logged with the run.
- **Decision required (triage §10.2):** confirm that the MCP autopilot tool's behaviour change is wanted. **Default if no answer: change it** — the research rules out the current default as a compliance defect.
- **Steps:** flip the three defaults; check `unified.py:457` and `:809` pass explicit values only where the caller intends execution.
- **Test:** `tests/test_autopilot_preview_default.py` — `create_task_run` with no argument returns `status == "previewed"` and does not call `execute_task_run` (monkeypatch it to a sentinel that fails).
- **Gate:** test above; HTTP leg `curl -s -X POST localhost:8000/nexus/autopilot/run` is **404 on the daemon** (route not served; EXECUTION G0). Record the MCP leg as **pending owner restart**.
- **Rollback:** flip the three defaults back.

---

## Track S1 — one outcome writer (keystone)

Plan step S1 = audit items FA-A2's writer side, FA-A3a, FA-A4, FA-A8. See `EXECUTION.md §4 S1` for the owner and inputs.

**The design rule (from triage §3):** one function (`learning.record_outcome`) is the only writer of `outcome` and counters. Every other site either calls it with an observed outcome or writes a non-terminal state. The existing guard (`learning.py` `TERMINAL_OUTCOMES` and `_normalize_outcome_label`) is the implementation; S1 makes the other call sites use it.

### S1-a · FA-A3a broker writes `executed` with a NULL outcome
- **Verified:** `broker_runtime.py:648-700` passes `outcome=None`.
- **Design:** a trace whose tool did not run is `dispatched` (not `executed`). A trace is `executed` only when a dispatcher observed the run — that dispatcher does not exist yet (S6), so until S6 no trace becomes `executed`.
- **Test:** `tests/test_broker_no_executed_without_run.py` — `execute_task_run` on a previewed run leaves every trace in `selected`/`dispatched`, never `executed`.

### S1-b · FA-A4 bridge derives `"correct"` from whether bytes came back
- **Verified:** `server/harness_mcp_bridge.py:166` — `"correct" if fused or payload.get("channels") else "partial"`.
- **Design:** the bridge does not judge outcomes. It returns the payload and `outcome = "unverified"`. The routing feedback comes from the caller's explicit `report_outcome`, not from the bridge.
- **Steps:** replace the expression; remove the coercion; keep the payload unchanged.
- **Test:** `tests/test_bridge_no_outcome_from_payload.py` — a non-empty payload returns `unverified`; an empty payload returns `unverified` (not `partial`).

### S1-c · FA-A8b `quality=float(quality or 1.0)` turns 0.0 into 1.0
- **Verified:** `server/execution_plane.py:341`.
- **Design:** `quality` is `None` (not measured) or a float (measured). `None` stays `None` in the record; the stored quality is NULL. `0.0` is preserved.
- **Test:** `tests/test_quality_zero_preserved.py` — `report_session(quality=0.0)` stores 0.0; `quality=None` stores NULL.

### S1-d · FA-A8/`ended_at` corruption (execution_plane)
- **Verified:** `execution_plane.py:243-246` calls `_update_persisted_session`, which sets `ended_at` on an active session.
- **Design:** `open_session` does not set `ended_at`. Only the close path does.
- **Test:** open a session; assert `ended_at IS NULL`.

### S1-e · `event_bridge` and `learning` writer duplicates
- **Design:** `event_bridge.record_outcome` delegates to `learning.record_outcome` or is removed after checking its two callers (`rl_feedback_loop.py:492`, `unified.py:740`). **[EXECUTION S1]**

**S1 gate:** G2 as SQL (EXECUTION §4): a non-terminal outcome leaves `resources.times_*` unchanged; a terminal outcome increments `times_correct`. Assert row values, not source text.

---

## Track S2 — verify registry (replaces the self-graded table)

Plan S2 = FA-A6 and the verifier half of FA-A8a. See `EXECUTION.md §4 S2`.

### S2-a · FA-A6 self-graded router
- **Verified:** `server/router.py:388-418` hand-written 56-entry table; call sites `router.py:995` (`route_task`) and `router.py:1265` (`_log_decision`).
- **Design [research: leakage / Goodhart arXiv:2207.07048, 1803.04585]:** the routing decision never receives a verdict from its own index. The table is deleted. `verify(task_type)` (S2) produces the outcome from a real exit code. With no verifier, the routing row is `routed` (non-terminal).
- **Steps:** rename to `_evaluate_outcome_ab_only` (offline A/B only) per EXECUTION S2, then delete it from the production path. Both call sites must go.
- **Test:** `tests/test_no_self_grade.py` — assert the production routing path writes `outcome_source` of `routed` or the verifier's value, and never the table's value. Assert on the written row.

### S2-b · FA-A8a verification returns constant `True`
- **Verified:** `execution_plane.py:300-336`.
- **Design:** the function returns the verifier's exit-code verdict or `skip`. `skip` is non-terminal and writes no counters.
- **Test:** `verify("general", "<dir with a broken .py>")` returns `wrong`; `verify("general", "<dir with a valid .py>")` returns `correct`; a timeout returns `skip`.

---

## Track T1 — stop fabricated attestations and random decisions

### T1-1 · FA-A5 orchestrator attests approval it never got
- **Verified:** `server/orchestrator.py:294-340` (phases marked done with `dispatched`; verification returns `True`), `:374-386` (prints "Not implemented", proceeds, writes `approved: True`).
- **Design [research: AU-3, AU-9]:** the approval gate is a hard refusal until an approval exists. Without an approval record, `run()` returns `status: "blocked_pending_approval"` and executes nothing. Verification returns `unverified` unless it checks that the declared outputs exist. `approved` is written only from an approval record, never a literal.
- **Steps:** replace the gate; replace the verifier's constant; change the phase status to `dispatched`.
- **Test:** `tests/test_orchestrator_fails_closed.py` — `run()` with no approval returns `blocked_pending_approval`, writes no `approved: True`, and calls no executor (sentinel).
- **Owner note:** `orchestrator.py` has one importer (`nexus-mcp-enhanced.py:484`). The MCP leg is pending owner restart.

### T1-2 · FA-A2 reflector decides retries with a seeded coin
- **Verified:** `server/self_reflector.py:236-296` (`_simulate_execution`, seeded from `times_correct / times_triggered`), called at `:242`.
- **Design [research: arXiv:1111.1797 — a point-mass posterior is a fixed random policy]:** remove `_simulate_execution`. `execute_with_retry` takes a real execution callable. Without one, it returns `unverified` and does not retry or update beliefs. Reflection consolidation (the four-copies problem) waits for this; **do not consolidate before the RNG is removed** (triage §5.1).
- **Test:** `tests/test_reflector_no_simulation.py` — `execute_with_retry` with no executor returns `unverified`; `random.random` is patched to raise, and the call still returns without raising.

### T1-3 · FA-A6 part 2 — remove self-grading from `route_task` completely
Covered by S2-a. Listed here so T1 is complete.

---

## Track S3 — one router (plan A1)

See `EXECUTION.md §4 S3`. The G0 decision (B: daemon gains `/nexus/*` aliases) applies.

- **Design:** one function behind HTTP (`/route`, `/nexus/route` alias), MCP (`find_skills`) and the broker (`route_for_task`). The v4 `route_task` body and the duplicate `route_to_clusters` (`router.py:224`, vs `cluster_router.py:78`) are removed. v5 is the only path behind `USE_UNIFIED_ROUTER=2`.
- **FA-C drift items that S3 resolves:** the router count (8) drops to v5 plus the classifiers that feed it. `context_assembly.py:44` (direct `router_v2` import) routes through the single function.
- **Test:** G1 as EXECUTION states: HTTP `/route`, MCP `find_skills` and `route_for_task` return the same top 3 for 10 fixed tasks; assert `result["skills"]`.
- **Pending:** the MCP leg is pending owner restart.

---

## Track T2 — security on live surfaces

### T2-1 · FA-B3 `ruflo_invoke` forwards any tool name
- **Verified:** `server/nexus-mcp-enhanced.py:1436-1462`.
- **Design:** an allowlist of ruflo tool names, held in `core/config` (not hard-coded in the MCP module). Unknown names return an error without dispatch. Denied by name even if listed: `terminal_*`, `browser_eval`, `config_set`, `system_reset` (from the execution audit). **The allowlist content is an owner decision** (see §10). The blast radius inside ruflo's catalogue is unmeasured (triage §8).
- **Test:** `tests/test_ruflo_allowlist.py` — a denied name returns an error and the proxy's `call_tool` is not called (sentinel).
- **Pending:** the MCP leg is pending owner restart.

### T2-2 · FA-B5 `mode` parameter widens its own permissions
- **Verified:** `broker_runtime.py:46-59` (`MODE_POLICIES["godmode"]` allows `operational`); `:79-80` silently maps unknown modes to `safemode`; `nexus-mcp-enhanced.py:805-806` takes `mode` from the caller.
- **Design [research: CM-6]:** the maximum mode is set by server configuration, not by the caller. A caller may request a *narrower* mode. An unknown mode is an error, not a silent downgrade. `godmode` is removed from the caller-selectable set.
- **Blocked-set note:** `operational` must be in the blocked set for non-god modes (triage §5, S6 — the S6 blocked set `{write, local_write}` omits `operational`).
- **Test:** `tests/test_mode_bounded_by_config.py` — `mode="godmode"` from the caller does not raise the ceiling; an unknown mode returns an error.

### T2-3 · FA-B2 unified binds `0.0.0.0`, no auth
- **Verified:** `server/unified.py:1171`; `start.sh:19`. The live daemon binds loopback (`nexus_http_daemon.py:935`, `supervisord.conf:48`).
- **Design:** bind `127.0.0.1` in both. Auth is not added while the server stays loopback-only; if LAN use is wanted, that is a separate decision with a token design (§10).
- **Test:** `tests/test_bind_loopback.py` — parse `start.sh` and `unified.py`'s `uvicorn.run` call; assert host `127.0.0.1`.

### T2-4 · FA-B4 supervisor and tmux name injection (unverified)
- **Status:** not opened by the triage. Do not rank until opened (triage §2).
- **Design, if confirmed:** names matched against `^[A-Za-z0-9_-]{1,64}$`; program names checked against those parsed from `supervisord.conf`; the `cmd` for `tmux new-session` built from a fixed table keyed by `ide_type`.
- **Step 0:** read `process_supervisor.py:183-233,:445` and `ide_manager.py:256-275`.

### T2-5 · FA-B1 SSRF in fetch paths
- **Verified:** `follow_redirects=True` at `channels/target_site.py:97`; `channels/scrapling_spider.py:119`; `channels/scrapecraft_pattern.py:89, :196, :296`. The triage counts six sites in four files. **Step 0: `grep -rn "follow_redirects" server` and reconcile the count before editing.**
- **Design [research: OWASP — disable redirect following; RFC 6890]:**
  1. A new `server/net_guard.py` (<150 lines) with `assert_public_url(url) -> pinned_ip`:
     - scheme must be `https` (decision: `http` allowed only on an explicit per-call flag);
     - resolve the host once with `getaddrinfo`;
     - reject if any resolved address is in: `10/8`, `172.16/12`, `192.168/16`, `127/8`, `169.254/16`, `100.64/10`, `0/8`, `::1/128`, `fc00::/7`, `fe80::/10`, `::ffff:0:0/96` (IPv4-mapped — check the embedded IPv4 too).
  2. Every site: `httpx.AsyncClient(follow_redirects=False)`. A 3xx response is followed **manually**: take `Location`, run `assert_public_url` on it, repeat, up to 5 hops.
  3. Pin the validated address for the connection, so DNS rebinding between check and connect fails (research: checking DNS separately does not stop rebinding). Implement by connecting to the pinned IP with `extensions={"sni_hostname": host}` so TLS still verifies the name.
  4. Retries and fallbacks reuse the same guard.
- **Tests:** `tests/test_net_guard.py` with a fake resolver:
  - `127.0.0.1`, `169.254.169.254`, `100.64.0.1`, `::1`, `::ffff:127.0.0.1` → refused;
  - a public address → allowed;
  - a redirect to a private address → refused on the second hop;
  - a resolver that returns a public address first and private second → refused (multi-record check).
- **Gate:** the six sites all import the guard (`grep -c net_guard`); the tests above; the harvest path returns `status` unchanged for a public URL in a test with the resolver stubbed.
- **Live state:** `harness_target_site` is exposed by the context-harness MCP server — **MCP leg pending owner restart**.

### T2-6 · FA-B8 scrapecraft reads a key from `~/.bashrc` and sends it to localhost
- **Verified (execution audit):** `scrapecraft_pattern.py:260-262`, `:313-314`.
- **Design:** the key comes only from the process environment, is sent only to an explicit allowlisted host, and is never logged. Reading `~/.bashrc` is removed.
- **Test:** the key is not read when the environment var is unset; the header is not sent to a non-allowlisted host.

### T2-7 · FA-B9 `forge` cross-project kill by prefix
- **Verified:** `forge.py:219-247` (prefix match `nexus-forge-{project}-`).
- **Design:** match the exact session name, derived from the project slug and seat index, with a delimiter the slug cannot contain.
- **Test:** project `a` does not match `nexus-forge-a-b-...`.

---

## Track T3 — seeders and data contamination

### T3-1 · FA-A12 `seed_benchmarks.py`, `seed_routing_log.py`
- **Verified:** both exist. The write path has not been run (triage §2).
- **Design:** after S1 (so there is one writer, and a seeded row is detectable), **audit before deleting**: `SELECT COUNT(*)` on each target table, and `SELECT ide, COUNT(*) FROM routing_log GROUP BY ide` to find seeded `ide` tags.
- **Decision (triage §10.3):** the 9 leaked `routing_log` rows (10350 → 10359) — clean up or leave. **Default: leave and document**, because deletion of live rows is irreversible.
- **Steps:** after the audit, delete both files in one commit with the audit numbers in the message.
- **Gate:** the suite; `routing_log` count unchanged by the commit.

---

## Track T4 — dead code (coverage-first, one file per commit)

Protocol from triage §6 and the research rule in §0.5. **Nothing in this track is decided yet.** Candidates are in `file-audit-2026-11-10.md §2` and are provisional.

1. Coverage: `coverage run --branch -m pytest tests`, then a real daemon request, then `coverage report --show-missing`. Keep files with 0 executed lines and 0 executed branches.
2. Entry points: enumerate `entry_points.txt`, `spec_from_file_location`, string `import_module` targets, `supervisord.conf`, `.mcp.json`, systemd units, `pyproject.toml`.
3. Import walk: `python -c "import <module>"` in the venv, plus a walk over `server/` for string imports.
4. One file per commit with its test edits; suite 490/24; `routing_log` unchanged.
5. **Blocked by design:** `reflection.py` is not deleted until T1-2 lands — its tests are the only specification of intended behaviour.

**Known candidates (provisional, each needs steps 1–3):** `core/router.py`, `server/learning_faithful.py`, `server/rl/` (three files and an `__init__`), `server/autonomous_pipeline.py`, `server/nexus_plan_executor.py`, `local_harness/harness.py` and `playbook.py`, `server/opencode_adapter.py`, `context_harness/backend_protocol.py`, `bundles/seeder.py`, `classifier/classification_pipeline.py`, `graphify_nexus_bridge.py`, and the ingest loaders behind the unused registry (`ingest/__init__.py:51-61`).

---

## Track T5 — latent fixes behind refusal tests

These paths have no live caller. Each gets a **regression test that the defect is refused**, and the fix is done when the path is revived.

| Item | Where | Design | Refusal test |
|---|---|---|---|
| FA-A9 ruflo reply matching | `ruflo_proxy.py:61-82` | Live via `ruflo_invoke` — **not latent**; fix in T5 only if T2-1 does not already cover it. Match JSON-RPC `id`; discard notifications; drain stderr to a log file | a stale reply with a different id is not returned |
| FA-A10 gcode store/exec | `gcode_backend.py:43-51, :69-74` | Delete `os.popen`; use `subprocess.run([...], shell=False)`. `store()` returns `error` until a real pipe exists | `store()` never returns `stored` without a write |
| FA-A11 sites orphan session | `local_harness/sites.py:78-104` | Wrap `bind_site` in try; on exception, close the broker row as `failed` | fault-inject `_insert_row` to raise; the session row is not left `active` |
| FA-A13 learnings leak across projects | `tool_session_broker.py:366-372`, `project_continuity.py:149-173` | Read `session.py` (178 lines, step 0); pass `project` into the query; filter at SQL | a row from project B is not returned for project A |
| FA-B7 / B8 ingest symlinks and transactions | `ingest/local_dir.py:26-62` | Precondition for reviving ingest, not a release blocker: `is_symlink()` skip, then `resolve().is_relative_to(root.resolve())` | a symlink to `/etc/hosts` in a configured dir is not indexed |

---

## Track T6 — preconditions on reviving ingest

Not scheduled. Any revival of `pipeline.py` / `get_loader` requires: the symlink containment (T5), a size cap on `read_text`, and trust markers on stored content (below).

### T6-1 · Untrusted-content markers (FA-B6 — stored harvested text)
- **Verified:** `research_ingest.py:85, :118, :160`; `nexus_bridge.py:81`; `engine.py:335-336`; `context_harness/server.py:842`.
- **Design [research: AU-3]:** a `trust` column (`NOT NULL DEFAULT 'untrusted'`) and `source_url`. Harvested content is `untrusted`. Serve-time wraps untrusted text in explicit delimiters so the consumer treats it as data.
- **Migration:** `db/migrations/022_…` — but **022 is taken by plan B1 (project_resources)**; use the next free number and record it in the commit (EXECUTION S7).

---

## 7. Ordering and gates, one table

| Order | Item | Depends on | Gate | Pending |
|---|---|---|---|---|
| 1 | T0-1 db fallback | — | suite 490/24, no `/tmp/nexus.db` | — |
| 2 | T0-2 sweeper `correct` | S0 (done) | new test | — |
| 3 | T0-3 `auto_execute` default | owner §10.2 (default: change) | new test + HTTP 404 note | MCP restart |
| 4 | S1 one outcome writer (S1-a…e) | T0-2 | G2 SQL | — |
| 5 | T1-1 orchestrator fails closed | S1 | new test | MCP restart |
| 6 | S2 verify registry (S2-a, S2-b) | S1 | G4, G5 | MCP restart |
| 7 | T1-2 reflector RNG removed | S2 | new test | — |
| 8 | S3 one router | S2 | G1 | MCP restart |
| 9 | T2-1 ruflo allowlist | owner §10 (content) | new test | MCP restart |
| 10 | T2-2 mode bounded | — | new test | MCP restart |
| 11 | T2-3 loopback bind | — | new test | — |
| 12 | T2-5 SSRF guard | Step 0 count | net_guard tests | MCP restart |
| 13 | T2-6, T2-7, T2-4 | step 0 reads | new tests | MCP restart |
| 14 | S4…S11 (EXECUTION.md) | S3 | per EXECUTION | — |
| 15 | T3-1 seeders | S1, table audit | counts unchanged | — |
| 16 | T4 dead code | entry-point enumeration | per file | — |
| 17 | T5 latent refusals | — | refusal tests | — |
| 18 | T6 ingest preconditions | — | — | not scheduled |

## 8. What this document does not decide

- **Owner decisions** (triage §10): scope of this pass (T0–T2 only, or the full sweep); `auto_execute` default; the 9 leaked `routing_log` rows.
- **The ruflo allowlist content** (T2-1) and whether `http` is ever allowed in `net_guard` (T2-5).
- **Whether LAN access is wanted** (T2-3). Default: loopback only.
- **FA-B4 and FA-A13** are not verified; their designs are conditional on the step-0 reads.
- Per-item blast radius: GitNexus covers a few symbols only. A call-graph pass for the S1 and S3 callers is a separate job before those commits.

## 9. Research basis

Design choices marked **[research]** cite `docs/audits/research-findings-2026-11-10.md`:
- NIST SP 800-53r5 CM-6 (most restrictive mode) and ASVS v5.0.0 V16.5.3 (fail-open) → T0-3, T2-2, the fail-closed rule.
- AU-3 (records carry success or failure indicators) and AU-9 (the audited component is not the authority on its own record) → the label invariant (§0.6), S1, T1-1.
- Beta-Bernoulli Thompson (arXiv:1111.1797) → T1-2.
- Leakage and Goodhart (arXiv:2207.07048, 1803.04585) → S2-a.
- Selective prediction and calibration (arXiv:1705.08500, 1706.04599, 2107.07511) → the vocabulary (`unverified`), and no confidence number on a routed outcome.
- OWASP redirect guidance and RFC 6890 → T2-5.
- Dead-code rule (coverage, entry points) → §0.5, T4.

Where a design leans on an unverified source, the research file's own disclosures apply: Biometrika 1933 full text returned 403 (metadata only), and `k = 60` in RRF is a pilot choice.
