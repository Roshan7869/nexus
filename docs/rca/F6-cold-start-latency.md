# F6 — Cold-Start Latency 14–21s · Performance Engineering

**Severity:** MEDIUM · **Effort:** LOW · **Blast radius:** First request per process · **Status:** OPEN

---

## 1. Symptom (observed)

The first `find_skills` / `cluster_route` after a fresh process takes **14–21 s**; every subsequent
call is ~50 ms. Live probes measured cold `find_skills` at 21,446 ms and cold `cluster_route` at
14,628 ms, then warm calls in tens of ms.

## 2. Root cause (mechanism)

A one-time, unavoidable-*if-lazy* model load happens on the **first** request instead of at boot.

- The semantic path routes through the process-wide `IsoEmbedder` singleton
  (`server/iso_embedder.py:30` `get()`), which lazily loads `all-MiniLM-L6-v2` on first use
  (`load_model()` `:60-68`). The class docstring itself calls per-call construction "the standard
  sentence-transformers anti-pattern" (`:17-20`).
- Cost breakdown: ~14 s one-time `import sentence_transformers` (Torch graph init) + ~1.4 s model
  weight load. Because `get()` is a singleton keyed by `realpath(db_path)` (`:32-44`), this is paid
  **once per process**; warm calls reuse the loaded model — hence the ~50 ms steady state.
- **A warmup exists but is misdirected.** `server/unified.py:50` pre-warms `TA_V4.intent_router`
  and `:1140-1153` pre-warms the *MCP module* in a background task — but **neither touches the v5
  `IsoEmbedder` singleton**. Nothing calls `IsoEmbedder.get().load_model()`
  at boot, so the Torch import is still deferred to the first user request. The in-process MCP
  `find_skills` has no warmup at all.

So the latency is a **placement** problem (fixed cost on the critical path) not a throughput bug.

## 3. Scientific grounding

- **Shahrad, M. et al. "Serverless in the Wild: Characterizing and Optimizing the Serverless
  Workload at a Large Cloud Provider." *USENIX ATC* 2020** — the canonical treatment of cold starts:
  the fix is a **pre-warming / keep-alive policy** that moves initialization off the request path.
  NEXUS's process is long-lived, so a single boot-time warm-up fully amortizes the cost.
- **Dean, J.; Barroso, L. A. "The Tail at Scale." *CACM* 56(2), 2013** — rare, large fixed costs
  (here the first-request 14 s) dominate tail latency; eliminating them at startup is the standard
  tail-latency mitigation.
- **Amdahl's Law (Amdahl 1967)** — a fixed serial startup segment caps achievable speedup; paying it
  once at boot (when no user waits) removes it from every request's critical path.
- **Model-serving warm-up (TensorFlow Serving warm-up requests; Crankshaw et al. "Clipper," *NSDI*
  2017)** — production inference systems issue synthetic warm-up requests at load time so the first
  real request is already hot. Directly applicable: fire one dummy `embed("warmup")` at boot.

## 4. Engineering context

The singleton is already correct; only the *timing* is wrong. Redirect the existing warm-up task to
touch the embedder, and add an equivalent warm-up hook to the in-process MCP entrypoint so the very
first `find_skills` is served hot.

## 5. Files / components to update

| Path | Current | Change | Annotation |
|------|---------|--------|------------|
| `server/unified.py:50` (embed pre-warm) + `:1140-1153` (MCP module warm) | Warms `TA_V4.intent_router` and the MCP module, **not** the v5 `IsoEmbedder` singleton | Also load + one `embed("warmup")` on `IsoEmbedder.get(db)` in the same background task | `RCA-F6` marker |
| `server/iso_embedder.py:30` `get()` | Lazy first-use load | No change (singleton correct); optionally expose `warmup()` helper | `RCA-F6` marker |
| `server/nexus-mcp-enhanced.py` (startup) | No embedder warmup | Add boot-time background warm-up of the singleton (non-blocking) | `RCA-F6` marker |

## 6. Execution steps

1. Add an `IsoEmbedder.warmup()` convenience: `self.load(); self.model.encode(["warmup"])`.
2. In `unified.py`'s background warm task, after MCP warm, call `IsoEmbedder.get(db).warmup()`.
3. In the enhanced MCP server startup, spawn a non-blocking thread that calls the same warmup so the
   stdio MCP surface is hot before the first tool call (don't block boot on it).
4. Add `RCA-F6` annotations at all three sites.

## 7. Verification (pass/fail)

- **PASS** iff, after boot completes + a short delay, the **first** `find_skills` returns in < 500 ms
  (measure cold-after-warmup) versus the 14–21 s baseline.
- **PASS** iff boot is not blocked (server answers a trivial tool within its normal startup window;
  warm-up runs in background).

## 8. Risk & rollback

- Low risk. Background warm-up must be exception-guarded so a warm-up failure never crashes boot
  (log ERROR, continue lazy). Rollback = remove the warm-up call; behavior reverts to lazy load.

## 9. References

1. Shahrad, M. et al. "Serverless in the Wild: Characterizing and Optimizing the Serverless Workload at a Large Cloud Provider." *USENIX Annual Technical Conference (ATC)*, 2020.
2. Dean, J.; Barroso, L. A. "The Tail at Scale." *Communications of the ACM* 56(2):74–80, 2013.
3. Amdahl, G. "Validity of the Single Processor Approach to Achieving Large-Scale Computing Capabilities." *AFIPS Conference Proceedings*, 1967.
4. Crankshaw, D. et al. "Clipper: A Low-Latency Online Prediction Serving System." *NSDI*, 2017.
