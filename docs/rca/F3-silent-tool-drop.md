# F3 — Silent Tool Drop on ImportError · Observability / SRE

**Severity:** MEDIUM · **Effort:** LOW · **Blast radius:** Contained to stale tree today; anti-pattern is systemic · **Status:** OPEN

---

## 1. Symptom (observed)

When the OKF engine import fails (F2), all six `okf_*` tools **vanish from the MCP surface with no
error surfaced to any operator**. The only trace is a `logger.warning` no one reads. The audit
noticed "10 tools" where code implies more, because the failed ones were silently dropped.

## 2. Root cause (mechanism)

Classic **fail-soft** anti-pattern — a hard capability contract violation degraded to a warning:

```python
# Downloads/nexus/server/nexus-mcp.py:469 → 562
try:
    from okf_graph_engine import ...
    @mcp.tool() def okf_ingest(...): ...      # 6 tools, :485-558
except ImportError as e:
    logger.warning(f"OKF graph tools unavailable: {e}")   # :562  ← swallowed
```

The process boots "successfully" advertising a tool set that is silently smaller than intended.
There is no boot-time assertion that the *expected* tool count was actually registered, so the gap
is invisible until someone calls a missing tool and gets "unknown tool".

## 3. Scientific grounding

- **Shore, J. "Fail Fast." *IEEE Software* 21(5), 2004** — a system should surface configuration/
  contract errors *immediately and loudly* at startup rather than limping in a degraded state. A
  swallowed `ImportError` is the textbook violation.
- **Candea, G.; Fox, A. "Crash-Only Software." *HotOS IX*, 2003** — components should either work or
  crash cleanly (then be restarted), never enter ambiguous partial states. A half-registered tool
  surface is exactly the ambiguous state crash-only design forbids.
- **Armstrong, J. "Making reliable distributed systems in the presence of software errors." PhD
  thesis, 2003 ("let it crash").** Erlang's philosophy: don't defensively swallow — fail visibly,
  supervise, restart. Applies to capability registration.
- **Google SRE (Beyer et al. 2016), monitoring/observability** — the "four golden signals" and
  startup invariants: a service must assert its own readiness contract, not assume it.

## 4. Engineering context

Production (`nexus-mcp-enhanced.py`) does not carry this specific block, so no live tool is dropped
today. But the *pattern* (bare `except ImportError: warn`) is the mechanism that let F2 hide for
~4 months, and it can recur anywhere tools are conditionally registered. The fix is a **boot-time
tool-count invariant** + converting silent warns into loud, structured startup errors.

## 5. Files / components to update

| Path | Current | Change | Annotation |
|------|---------|--------|------------|
| `Downloads/.../server/nexus-mcp.py:561-562` | `except ImportError: warn` | Removed with F2 | N/A |
| `server/nexus-mcp-enhanced.py` (startup) | No count assertion | Add `assert_expected_tools(EXPECTED_MIN)` after registration; log the delta of any optional-tool group that fails to load | `RCA-F3` marker |
| `server/harness_mcp_bridge.py` (~265-317, bridge registration) | Best-effort bridge load via a broad `except Exception as exc` (~:316) — swallows the reason | Emit structured `tool_registration` event per group (loaded/skipped + reason) | `RCA-F3` marker |

## 6. Execution steps

1. Define `EXPECTED_MIN_TOOLS` (currently 99) as a module constant in the enhanced server.
2. After all `@mcp.tool()` registration, count registered tools; if `< EXPECTED_MIN_TOOLS`, log a
   **structured ERROR** (`{event:"tool_registration_shortfall", expected, actual, missing:[...]}`)
   and — for required groups — raise. Optional groups log ERROR but may continue.
3. In the harness bridge, the load is already wrapped in a broad `except Exception as exc` (~:316)
   that warns without structure — replace that warn with a per-group structured
   log recording which group loaded and, on failure, the exception + that the group is optional.
4. Add the `RCA-F3` annotation markers at both edit sites.

## 7. Verification (pass/fail)

- **PASS** iff removing one tool registration and rebooting produces a visible `tool_registration_shortfall`
  ERROR (not silence). Test by temporarily gating one optional group behind a false flag.
- **PASS** iff normal boot logs `{event:"tool_registration", loaded:99, ...}` once.

## 8. Risk & rollback

- Low risk. Raising on *required* shortfall could stop boot — scope the hard-raise to genuinely
  required groups; keep optional groups as loud-but-continue. Rollback = revert the assertion block.

## 9. References

1. Shore, J. "Fail Fast." *IEEE Software* 21(5):21–25, 2004.
2. Candea, G.; Fox, A. "Crash-Only Software." *Proc. 9th Workshop on Hot Topics in Operating Systems (HotOS IX)*, 2003.
3. Armstrong, J. *Making Reliable Distributed Systems in the Presence of Software Errors.* PhD thesis, KTH, 2003.
4. Beyer, B. et al. *Site Reliability Engineering*, ch. "Monitoring Distributed Systems." O'Reilly, 2016.
