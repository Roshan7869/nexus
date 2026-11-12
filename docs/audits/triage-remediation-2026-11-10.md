# Triage — file audit 2026-11-10, re-measured against source

Status: triage complete, no code changed. Companion documents: `file-audit-2026-11-10.md`
(the audit), `../plans/remediation/EXECUTION.md` (the remediation plan, S0 executed).
Research child `deleg_42b52d5e` is producing primary-source tables for Q1–Q7; citations are
folded in on return, and nothing below depends on them.

## 0. Naming collision — read this first

The audit numbers its findings `A1 … D`. The remediation plan already numbers its 14 changes
`A1 … E2`. They are different sets with different meanings. **The audit's ids are renamed
`FA-<n>` here**; the plan keeps its own. Do not merge the documents without this mapping:

| Audit id | Plan id | Relationship |
|---|---|---|
| FA-A1 learner methods | — | not in the plan |
| FA-A2 random retry | plan A3 (verify registry) | plan A3 is the intended fix |
| FA-A3 fake broker outcome | **plan A2 (one outcome writer)** | FA-A3 is the *evidence* for plan A2 |
| FA-A4 bridge default | — | not in the plan |
| FA-A5 orchestrator | — | not in the plan |
| FA-A6 self-graded router | plan A3 | same target, different mechanism |
| FA-A7 feedback CLI | — | dead code (see §3) |
| FA-B* security | — | **no plan item covers these at all** |
| FA-C drift | plan A1 + C1 | plan A1 cuts duplicate entry points |
| FA-D hygiene | — | not in the plan |

## 1. What "verified" means in this document

Every row marked CONFIRMED was opened in source at the cited line by the agent writing this
document, after the audit. A repo-wide grep is not evidence: `get_route|get_learning_stats`
returns 40 hits and still does not show either method on `NexusLearner`, because
`get_learning_stats` exists at module level. Class-body membership was checked instead.
One grep was truncated mid-line at 1800 chars and made `git_repo.py:33` look like a syntax
error; the file parses. Truncated tool output is not evidence either.

## 2. Verdicts

| id | audit claim | verdict | evidence I opened | effect on rank |
|---|---|---|---|---|
| FA-A1 | `learner.get_route` / `get_learning_stats` missing | **CONFIRMED, but LATENT** | `core/server.py:95–112`, `:342–356`; neither method in the `NexusLearner` class body | **down.** Gated by `learner.config.routing.enabled` (`core/server.py:98`); `RoutingConfig.__init__(enabled=False)`, `NexusConfig.from_dict` defaults `False`, and `config/default.json` has no `routing` block at all. Unreachable unless routing is switched on in code. |
| FA-A2 | reflector picks outcomes at random | **CONFIRMED** | `server/self_reflector.py:236–296`; coin seeded from `times_correct / times_triggered` | hold. Also: a never-run tool is judged by a ratio it never earned. |
| FA-A3a | broker marks sessions "executed" with no outcome | **CONFIRMED as a ledger lie, not as belief corruption** | `broker_runtime.py:648–700` passes `outcome=None`; `learning.py` `_normalize_outcome_label(None)` is not terminal → early return, telemetry row only | **down one notch.** The `executed` status and a NULL `execution_log.outcome` are real; no counter and no posterior moves. |
| FA-A3b | timeout sweeper hard-codes "correct" | **CONFIRMED, this is the harmful half** | `broker_runtime.py:1072–1090`, `outcome="correct"`, `outcome_source="timeout"` | **up.** Terminal label → counters and Thompson sampling update on a guess. This is the belief corruption. |
| FA-A3c | auto-execute on by default | **CONFIRMED and reachable** | `nexus-mcp-enhanced.py:806` `auto_execute: bool = True`; only two callers of `create_task_run` (`:809`, `unified.py:457`) | **up.** The MCP tool is a live surface. `unified.py:457` is not running. |
| FA-A4 | bridge coerces outcome | **CONFIRMED, worse than a default** | `harness_mcp_bridge.py:166` `"correct" if fused or payload.get("channels") else "partial"` | **up.** A verdict derived from *whether data came back*, not from what it said. |
| FA-A5 | orchestrator approval gate unimplemented | **CONFIRMED, worse** | `orchestrator.py:294–340`, `:374–386`: prints "Not implemented", proceeds, writes `approved: True` | **up.** Fabricated attestation, not a missing feature. |
| FA-A6 | router grades itself | **CONFIRMED** | `router.py:388–418`, 56-entry hand table | hold. |
| FA-A7 | CLI feedback path broken | **CONFIRMED but DEAD** | `feedback.py:1–48`, `:48–92`; `sys.path.insert(0, ~/nexus)` where no `learning.py` exists; `WHERE outcome IS NULL` never matches rows now written as `routed` | **down to dead-code track.** Zero importers in `server/`, `core/`, `tests/`, `scripts/`; only its own `__main__` at `:73`. |
| FA-A8a | verification returns constant True | **CONFIRMED** | `execution_plane.py:300–336` | hold. |
| FA-A8b | quality forced to 1.0 | **CONFIRMED** | `execution_plane.py:341` `quality=float(quality or 1.0)` — `0.0` is falsy, so a measured 0.0 becomes 1.0 | hold. |
| FA-A9 | proxy reply matching | **CONFIRMED by absence** | `server/ruflo_proxy.py:61–82`; `_read_response` returns the first parseable JSON line and never inspects `id` | hold. Also swallows `notifications/*`. |
| FA-A10 | gcode store/exec | **CONFIRMED, two defects** | `gcode_backend.py:43–51` `os.popen(f"{GCODE_BINARY} {' '.join(args)}")` — unescaped; `:69–74` `store()` never passes `value` to `memory import -` (stdin) yet returns `status: stored` | hold, latent behind `_gcode_available()`. |
| FA-A11 | local site failure leaves orphan session | **PARTIAL** | `local_harness/sites.py:78–104` — `lh.effect(...)` inverse is registered on failure; settle path partially defended | **down.** Needs a fault-injection test, not a rewrite. |
| FA-A12 | seeders fabricate data | **file exists, write path not executed** | `scripts/seed_benchmarks.py`, `scripts/seed_routing_log.py` exist | hold; check the target tables before deleting. |
| FA-A13 | global learnings leak into project sessions | **UNVERIFIED** | `local_harness/session.py` (178 lines) read for existence only | unranked until read. |
| FA-B1 | SSRF in fetch paths | **CONFIRMED, wider than the audit says** | `channels/target_site.py:90–97` (`httpx.AsyncClient(..., follow_redirects=True)`), `:131–137` (`client.get(url)` with `netloc` used only for rate limiting and rules); `follow_redirects=True` also at `channels/scrapling_spider.py:119` and `channels/scrapecraft_pattern.py:89, :196, :296` — **six call sites in four files, not three** | hold. Redirect-following means an initial-URL allowlist is insufficient — each hop must be re-checked, and the fix has to reach all six sites or the three the audit missed become the path of least resistance. |
| FA-B2 | `unified.py` binds 0.0.0.0, no auth | **CONFIRMED, latent** | `unified.py:1171` `uvicorn.run(app, host="0.0.0.0")`; `start.sh:19` `--host 0.0.0.0`; the running daemon defaults to loopback (`nexus_http_daemon.py:935 --host default 127.0.0.1`). No auth on either; identity is caller-supplied | hold as latent. Two-line fix while it stays latent — after it starts, it is remote code execution via the launch/bootstrap endpoints, not a bind-address nit. |
| FA-B3 | `ruflo_invoke` has no allowlist | **CONFIRMED (mechanism)** | `nexus-mcp-enhanced.py:1436–1462` forwards any `tool_name`; docstring advertises the whole catalogue | hold; the blast radius inside ruflo's catalogue is **unmeasured**. |
| FA-B4 | supervisor/tmux name injection | **UNVERIFIED** | not opened | unranked. |
| FA-B5 | `mode` filter is not authorization | **CONFIRMED, worse: authorization by parameter** | `broker_runtime.py:46–59` `MODE_POLICIES["godmode"]` allows `{"read","local_write","external_write","operational"}`; `:79–80` any unknown mode silently becomes `safemode`; `mode` arrives as a client parameter (`nexus-mcp-enhanced.py:805–806`) | hold and link to S6. A caller passes `mode="godmode"` and widens its own side-effect budget with no principal check. S6's blocked set `{write, local_write}` does **not** contain `operational`, so the planned gate would not block the one level godmode unlocks. |
| FA-B7 | ingest follows symlinks | **CONFIRMED, but LATENT** | `ingest/local_dir.py:26–62`: `rglob("*")` does not descend into symlinked dirs, but does yield symlinked files and `is_file()` follows them | **down.** `ingest.get_loader` is called only from `pipeline.py:111`, and `pipeline.py` has **no importer**. The live `get_loader` is the unrelated `lazy_loader.get_loader`. |
| FA-B8 | ingest not transactional | **CONFIRMED, LATENT** | same path | down, same reason. |
| FA-D1 | `db.py` falls back to `/tmp` | **CONFIRMED, under-rated by the audit** | `db.py:69–76`: on unwritable DB, copies to a predictable `/tmp/nexus.db` and uses it if present | **up.** Two problems: a full copy of live data in a world-writable path, and a pre-existing `/tmp/nexus.db` is trusted without verification when the copy fails (the exception is swallowed). |
| FA-D2 | `routing_log` rebuild drops columns | **CONFIRMED but already applied** | `db.py:186–243`, guarded by `elif not already_relaxed`; the INSERT list omits `action_type`, `repeat_count_1h` | **down to a note.** Historical data loss, one-time. |

**The audit contradicts itself, and the contradiction is resolvable.** FA-B7/FA-B8 describe the
ingest path as a live security defect; FA-D3 lists the five ingest loaders, behind that
registry, as dead code. Both cannot hold. Measured: the pipeline is unreachable, so the security
items are latent and D is right. Keep B7 as a **precondition on reviving ingest**, not as a
release blocker.

## 3. The one diagnosis

Seven items — FA-A1, A2, A3a, A4, A5, A6, A8 — are one defect wearing seven costumes: **a
component writes a terminal label it did not observe.**

    get_route returns nothing        → caller invents a route
    reflector has no result          → invents one with a seeded coin
    broker never ran the tool        → writes status "executed"
    sweeper timed out                → writes outcome "correct"
    bridge got bytes back            → writes outcome "correct"
    orchestrator skipped the gate    → writes approved: True
    verifier cannot verify           → returns True

The codebase already contains the correct implementation of the principle, in one place:
`learning.py` `TERMINAL_OUTCOMES` + `_normalize_outcome_label`, which turns an unobserved
outcome into `routed` telemetry and **returns before touching a counter**. So the fix is not a
verification framework. It is: **make the other six call sites go through that existing guard,
and delete the code that fabricates instead of routing the absence to it.** That is what plan
S1 already is; the audit is the evidence that S1 is the keystone and not one of fourteen peers.

Corollary for the label vocabulary: `partial` and `unverified` must be *states*, never scores.
FA-A4's `"partial"` for an empty payload and FA-A8's `float(quality or 1.0)` are the same bug —
a missing measurement promoted to a number.

## 4. Live, latent, dead

| state | items | why it matters |
|---|---|---|
| **Live** | FA-A2, A3b, A3c, A6, D1 | reachable from an IDE MCP call or a live daemon path today |
| **Latent** | FA-A1, A3a, A4, A9, A10, A11, B1, B2, B3, B5, B7, B8 | real defects, no live caller: routing disabled, `unified.py` not running, ingest unreachable, gcode absent |
| **Dead** | FA-A7, D3 and the ~25 files | no importer; has no rank until someone decides to revive it |

The audit's ordering interleaves these. Live and latent are different jobs: live defects get
fixed and gated now; latent ones get a **regression test plus a refusal**, and are fixed when
their path is revived. That single distinction reorders most of the audit.

## 5. Re-ranked sequence

The audit's suggested order — runtime paths, then SSRF and loopback, then seeders, then one
router and one reflection path — is defensible as a list of *subjects*. It is the wrong order for
*work*, for four reasons the audit could not see: it did not read the remediation plan, it did not
check reachability, it did not check which processes can be restarted, and FA-A7/FA-B7 turn out to
be dead/unreachable.

Merged sequence. `S*` are the plan's existing steps; `T*` are new tracks this triage adds.

    T0  FA-D1  /tmp fallback → refuse, do not degrade          (10 lines, no gate needed)
    T0  FA-A3b sweeper + FA-A3c auto_execute default           (live belief corruption)
        └ both land before the suite runs again: they write to the live DB
    S1  plan A2 = FA-A3a/A4/A8  one outcome writer             (keystone, unchanged)
    T1  FA-A5  orchestrator: refuse instead of attesting
    T1  FA-A6  delete the 56-entry table + FA-A2 seeded coin   (verifier, not a table)
    S2  plan A3  verify registry (coding, general)
    S3  plan A1  delete duplicate router entry points  ← FA-A1 here, not first
    T2  FA-B3  ruflo_invoke allowlist   FA-B5  principal check  FA-B2 loopback bind
    S4…S11 unchanged, with S6 gating on `resource_policies`
    T3  FA-A12 seeders: delete after table audit
    T4  dead code: coverage-first, one file per commit (§6)
    T5  latent fixes gated by a refusal test: FA-A9, A10, A11, A13
    T6  FA-B7/B8 as preconditions on any ingest revival

### 5.1 Where this order disagrees with the audit, and why

- **FA-A1 is not first, it is S3.** It is gated off (`RoutingConfig(enabled=False)`), so it
  cannot crash a request today. Doing it first buys a working `/nexus/beliefs` nobody asked for
  and spends the commit budget before the live writers are fixed. It also lands naturally with
  plan A1's deletion of duplicate entry points, one diff, one rollback.
- **FA-A7 leaves the runtime track entirely.** Zero importers. Fixing its import and its query
  is work on a script nothing calls, while the writer it was supposed to read is being rewritten
  in S1. It goes to T4 with a note: if the feedback loop is wanted, it is *new* code against the
  post-S1 schema, not a repair.
- **FA-B7/B8 leave the release-blocker list** and become preconditions, because the path is
  unreachable. Fixing security on dead code and then deleting the dead code is wasted work.
- **Seeders come after S1, not before.** Deleting `seed_routing_log.py` while a second writer
  still exists leaves the reason it was written unaddressed; S1 makes the deletion safe.
- **The audit's "then pick one router and one reflection path" is two jobs.** Router
  consolidation is plan A1/S3 (deletions, mechanical). Reflection consolidation is T1 (behaviour:
  the RNG must die first, or you consolidate four copies of a fabrication).

## 6. Dead-code deletion, if it is done at all

Name-based importer counts are not sufficient in this codebase: `ingest/__init__.py` loads
loaders through `__import__` under string keys, and the same file name maps to two different
`get_loader` functions. Protocol, per file:

1. `coverage run --branch -m pytest tests` then a real route through the daemon, then
   `coverage report --show-missing` and keep only files with **0 executed lines and 0 executed
   branches**. A file that a test imports but never executes is not dead by coverage either —
   reconcile both lists.
2. `python -c "import <module>"` in the venv, plus an import-walk over `server/` to catch string
   imports.
3. Delete one file per commit with its test edits, `490/24` green, and `routing_log` unchanged.
4. Files the audit flags as needing a decision stay until the decision is written down, not
   inferred from silence.

Do **not** delete `reflection.py` on the strength of "test-only importer" until T1 lands: the
tests are the only specification of its intended behaviour, and T1 needs them.

## 7. Gates, and which ones can be verified at all

- HTTP leg: `systemctl --user restart nexus-daemon && curl -s localhost:8000/health`.
- **MCP leg: cannot be verified live.** Four `nexus-mcp-enhanced` processes are IDE-owned;
  `CLAUDE.md:128` forbids restarts. Any FA-A3c, FA-A4 or FA-B3 fix is verifiable only in a fresh
  pytest process, and must be recorded as **pending owner restart**, never as passing.
- DB leg: `routing_log` total, currently **10359**, plus `ide='pytest'` currently **71**. Not
  `md5sum` — a 624 MB WAL database with a live writer has no stable hash.
- Suite: forward and reverse order must both read **490 passed / 24 skipped**. Reverse needs
  `find tests -name 'test_*.py' | sort -r`; `ls` misses seven subdirectories.

## 8. Not verified, and what it would cost

| item | cost |
|---|---|
| FA-A13 (global learnings leak) | read `local_harness/session.py` (178 lines), one test |
| FA-B4 (supervisor/tmux injection) | two files, plus deciding whether a name is trusted input |
| FA-B3 blast radius inside ruflo's catalogue | needs the MCP process restarted; owner action |
| FA-A12 target tables | `SELECT COUNT(*)` on each table the seeders write |
| Blast radius of every change | the audit says GitNexus covers few symbols; a real call-graph pass is a separate job |
| FA-C drift numbers (8 routers, 4 reflection, 3 RRF, 3 FAISS) | a targeted count each; not done here |

## 9. Research fold-in — primary sources, every identifier loaded

Full tables in `research-findings-2026-11-10.md` (203 lines, 62 rows, 11 arXiv ids, 2 disclosed
`UNVERIFIED`). Only the rows that **change a decision here** are carried up.

- **T0 defaults are the prohibited shape, not a style choice.** NIST SP 800-53r5 CM-6 requires
  "the most restrictive mode consistent with operational requirements"; ASVS v5.0.0 V16.5.3 names
  fail-open explicitly — "processing a transaction despite errors resulting from validation
  logic" — and SP 800-160v1r1 D.4.3 states the governing principle as protective failure. So
  `auto_execute: bool = True` (`nexus-mcp-enhanced.py:806`) and the `/tmp` fallback (`db.py:69–76`)
  are the named defect, not merely risky. Caveat from the same source: 800-160 does **not** mandate
  unconditional halt — "Other stakeholder objectives may necessitate the continuation of operations
  in a less-than-fully-secure state" — so the rule is deny-by-default with a documented opt-in, not
  a permanent crash.
- **Fabricated labels violate AU-3, and self-policing violates AU-9.** An audit record must carry
  "indicators of event success or failure"; the audited component may not be the authority on its
  own record. `approved: True`, `outcome="correct"` for a timeout, and `quality=float(quality or 1.0)`
  are all AU-3 violations, which is a stronger statement than "misleading".
- **The seeded coin is worse than random** (FA-A2). Beta-Bernoulli Thompson updates
  `α += reward, β += 1-reward` and draws `p̃ ~ Beta(α,β)`; drawing at `p̂ = times_correct/times_triggered`
  collapses the posterior to a point mass, cannot distinguish 3/3 from 300/300, and with no observed
  outcome never leaves the prior. The policy is a fixed random policy wearing learning's clothes
  (arXiv:1111.1797; NIPS 2011 baseline paper). A2's rank stands, its description changes.
- **FA-A6 gets a term of art.** Grading against the same 56 entries it indexes is leakage
  (arXiv:2207.07048, 8-type taxonomy); self-graders prefer their own output and the bias scales with
  self-recognition (arXiv:2404.13076); judge bias is a replicated family (arXiv:2306.05685). Use
  "specification gaming" / Goodhart (arXiv:1803.04585) in the report rather than "circular".
- **FA-B1's fix changes shape.** OWASP's cheapest correct answer is to stop following redirects —
  "Disable the support for the following of the redirection in your web client in order to prevent
  the bypass of the input validation." With six sites setting `follow_redirects=True`, the fix is
  `follow_redirects=False` **plus** validation against the resolved address pinned at connect time;
  checking DNS separately does not stop rebinding, and retries/fallbacks need the same policy. Deny
  list must cover RFC1918, 127/8, 169.254/16, 100.64/10, `::1/128`, `fc00::/7` and `::ffff:0:0/96`
  (RFC 6890). A four-block private check misses CGNAT and every IPv6 form.
- **§6's protocol gains a rule: "zero importers" is not evidence here.** `importlib.import_module`
  string targets, `importlib.util.spec_from_file_location`, PyPA `entry_points.txt`, and coverage's
  own documented limit — a module "had already been imported when coverage started" — each defeat a
  grep. Enumerate `*.entry_points.txt` and `spec_from_file_location` call sites *before* deleting
  anything; one file per commit; ordering by size or age is inference, not sourced.
- **Vocabulary, settled (FA-A4, A5, A8).** The honest label is `unknown`/`unverified`; selective
  prediction rejects rather than guesses (arXiv:1705.08500). Do **not** attach a confidence number to
  a routed outcome — stated confidence is not P(correct) (arXiv:1706.04599), so "we abstain when
  unsure" is a heuristic and must never be called calibrated (conformal guarantees need exchangeable
  labelled calibration data, arXiv:2107.07511).
- **Disclosures carried, not hidden:** Biometrika 1933 full text returned 403 — DOI, volume, pages and
  date verified via Crossref, no body text quoted. Contested points are recorded in the findings
  file, including that `k = 60` is a pilot choice ("not critical") not a tuned constant, and that RRF
  reads ranks only, so normalising scores is harmless but pointless.

## 10. Owner decisions

1. **Scope of this pass**: live defects only (T0, S1, T1, T2) or the full T0–T6 sweep? The
   first is roughly four commits and is defensible; the second touches dead code the audit
   itself says needs per-file decisions.
2. **FA-A3c**: flipping `auto_execute` to `False` changes behaviour for the MCP autopilot tool.
   Confirm that is wanted, or keep `True` and gate on a real outcome instead.
3. **The 9 leaked rows** in `routing_log` (10350 → 10359) are still there. Cleanup or leave.
   The 624 MB backup is at `~/.hermes/cache/scratch/nexus.before-pytest.db`.
