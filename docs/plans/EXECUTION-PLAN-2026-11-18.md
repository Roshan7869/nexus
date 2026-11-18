# NEXUS Execution Plan — tooling & configuration to unblock and boost

**Date:** 2026-11-18 · **Branch:** second-brain-v2 · **Machine:** Windows 11 Home 26200, i5-1135G7
**Measured, not assumed.** Every constraint below was probed on this machine on 2026-11-18.

---

## 1. Measured system baseline

| Resource | Value | Consequence |
|---|---|---|
| CPU | Intel i5-1135G7, 4C/8T, 2.4 GHz base (observed 1.38 GHz) | CPU inference only; low-power laptop chip — budget tightly |
| RAM | 15.7 GB total, **5.6 GB free** | Cannot run server + model + IDE comfortably in parallel |
| GPU | Intel Iris Xe, 2 GB, **no CUDA** | No GPU acceleration for torch/ONNX-CUDA |
| Disk C: | 115.6 GB free of 199.7 | Fine, but OneDrive-hosted |
| Disk **D:** | **263.6 GB free, empty, NTFS, writable** | Use for venv, models, DB, index |
| OS | Windows 11 Home 26200 | No Hyper-V admin guarantees; supervisord/systemd unavailable |
| WSL | WSL2 feature present, **no distro installed** | Linux-only tooling unavailable unless a distro is installed (~2 GB) |

### Runtimes — as found

| Tool | State | Impact |
|---|---|---|
| `python` / `python3` | **Windows Store alias stub** ("Python was not found") | pip/venv unusable; this is the #1 blocker |
| DSH bundled Python | 3.12.14, numpy + pandas only | Missing everything NEXUS needs |
| NEXUS Python deps | **fastapi, pydantic, yaml, dotenv, httpx, pytest, fastmcp, sqlite-vec, requests all ABSENT** | Server cannot start |
| `node` / `npm` | v24.22.1 / 11.19.1 (via `npm.cmd`; `npm.ps1` blocked) | Node tooling works with a bypass |
| `git` | 2.56 — Schannel broken, needs `http.sslBackend=openssl` | Affects every clone/fetch |
| `cargo` / `rustc` | **absent** | Rust tooling unavailable |
| **MSVC `link.exe`** | **absent** | **Rust cannot build even after `rustup`** (needs VS Build Tools ≈3.6 GB) |
| `docker`, `ollama`, `uv`, `pip`, `sqlite3` | absent | — |
| `winget` | **present** | Install channel available |

### Network — all install sources reachable

`pypi.org` 200 · `huggingface.co` 200 · `registry.npmjs.org` 200 · `github.com` 200 · crates.io reachable · `api.openalex.org` 200

---

## 2. Corrections to earlier advice (from measurement)

1. **`fastembed-rs` is NOT a near-term option.** Rust needs `link.exe`; there are no MSVC build tools. Building Rust here means a ≈3.6 GB Visual Studio Build Tools install. **Use the Python `fastembed` package instead** — same ONNX model zoo, same runtime engine, ~150 MB, no torch, no Rust.
2. **Do not install `sentence-transformers` + `torch`.** ≈2.5 GB and no GPU to use it. `onnxruntime` gives the same 384-dim MiniLM vectors at a fraction of the size.
3. **`db/nexus.db` currently sits inside OneDrive.** SQLite WAL + a file-sync agent is a real corruption/lock hazard. Move runtime state to `D:`.

---

## 3. Hard constraints the plan must respect

| # | Constraint | Rule that follows |
|---|---|---|
| C1 | No real Python toolchain | Everything blocks on installing `uv` + CPython first |
| C2 | No MSVC linker | No Rust builds. ONNX/Python path only |
| C3 | 5.6 GB free RAM | One server, one model, `--workers 1`; no torch |
| C4 | No CUDA | CPU-only. 384-dim MiniLM is the ceiling; avoid 1024-dim models |
| C5 | OneDrive workspace | Move DB/artifacts/models to `D:\nexus-data`; exclude `nexus/` from sync |
| C6 | No supervisord/systemd | Use Task Scheduler or DSH jobs for long-lived processes |
| C7 | NEXUS hardcodes `/home/roshan` in 78 files | Path shim via env, not mass edits |
| C8 | `process_supervisor.py` shells to `pgrep`/`systemctl` | Windows health checks need a different probe |
| C9 | Cloudflare blocks SSRN/Elsevier/etc. | Research goes via Crossref/OpenAlex APIs (already wired) |

---

## 4. Execution plan

### Phase 0 — Unblock the machine (~40 min, ~1.5 GB) — NO PREREQUISITES

| Step | Command | Why |
|---|---|---|
| 0.1 | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` | Unblocks npm.ps1 and all PS scripts (no admin needed) |
| 0.2 | `winget install --id astral-sh.uv -e` | Replaces pip/venv/pyenv/pipx in one tool; brings its own CPython |
| 0.3 | `git config --global http.sslBackend openssl` | Fixes the Schannel failure for every repo, permanently |
| 0.4 | Create `D:\nexus-data\{db,artifacts,models,index,logs}` | Gets runtime state out of OneDrive (C5) |
| 0.5 | Point `.env` at `D:\nexus-data\...` for `NEXUS_DB_PATH`, `NEXUS_ARTIFACTS_DIR`, `NEXUS_INDEX_PATH` | C5 |
| 0.6 | `setx HUGGINGFACE_HUB_CACHE D:\nexus-data\models\hf` and `setx FASTEMBED_CACHE_PATH D:\nexus-data\models\fastembed` | Models never land on C: |

**Gate:** `uv --version` works; `uv python list` shows a CPython; `git ls-remote` on a fresh HTTPS repo succeeds without the `-c` flag.

### Phase 1 — Install a minimal, working NEXUS runtime (~30 min, ~450 MB)

| Step | Command | Notes |
|---|---|---|
| 1.1 | `uv venv D:\dev\nexus-venv --python 3.12` | venv on D:, never in OneDrive |
| 1.2 | `uv pip install fastapi uvicorn pydantic pyyaml python-dotenv httpx pytest pytest-asyncio numpy sqlite-vec fastmcp mcp` | The server + MCP actually need these |
| 1.3 | **Defer** `torch`, `sentence-transformers`, `faiss-cpu` | C2/C3/C4 — superseded by Phase 2 |
| 1.4 | `uv run pytest tests/ -q` | First real baseline. Expect failures from `/home/roshan` paths (C7) |
| 1.5 | `uvx ruff check .` and `uvx black --check --line-length 100 .` | Repo already configures black at 100; ruff catches real defects |

**Gate:** `uv run python -c "import server.unified"` imports; pytest collection succeeds; a test-count baseline is recorded (prior estimate ~692 functions).

### Phase 2 — Embeddings without torch (~1 h, ~250 MB) — **the highest-value step**

| Step | Command / change | Why |
|---|---|---|
| 2.1 | `uv pip install fastembed onnxruntime` | Same ONNX model zoo as fastembed-rs, no MSVC, no torch |
| 2.2 | Model: **`all-MiniLM-L6-v2` (384-dim)** first | **Matches the existing FAISS index and `bit[384]` sqlite-vec lane — zero re-embed** |
| 2.3 | Add an ONNX backend to `server/iso_embedder.py` behind a flag (keep the sentence-transformers path) | Additive; the file is currently import-fatal (`sys`/`Any` undefined at line 34) |
| 2.4 | Backfill the 27 absorbed resources, then all others | Unlocks the semantic lane that is currently empty |
| 2.5 | Verify: cosine search returns `egm-structural` for "pedantic quality auditor" | Lexical FTS cannot do this; semantic can |

**Deferred:** `bge-small-en-v1.5` (better MTEB) — requires re-embedding every vector **and** query-prefix discipline. Only after Phase 2 is proven.
**Rejected for now:** `fastembed-rs` sidecar (C2), `TEI` (adds a port, violates the no-new-port contract), `model2vec` (great for latency, but its Python path is fine to pilot later).

**Gate:** ≥95% of active resources have vectors; p50 embed latency <30 ms/text on this CPU; FTS and vector lanes both return results in one query.

### Phase 3 — Populate the intelligence inputs (~2 h, data only)

Order is mandatory — each step feeds the next:

| Step | Action | Feeds |
|---|---|---|
| 3.1 | `uv run python scripts/absorb_repo.py <repo>` for each repo | `resources` |
| 3.2 | Seed `cluster_definitions`, assign `cluster` + `cluster_membership` | unlocks `NEXUS_CLUSTER_ROUTING` and v5's L1 short-circuit |
| 3.3 | `scripts/densify_kg.py` / `kg_rebuild` | `resource_edges` → makes the graph lane non-empty |
| 3.4 | Real `task_complete` reports from live sessions | `outcome_log` → gives Thompson actual beliefs |

**Gate:** clusters ≥1 with ≥20 members; edges >0; outcome rows >0; then — and only then — tune `NEXUS_V5_W_*`.

### Phase 4 — Harness wiring / productivity (~1.5 h)

| Step | Action | Gain |
|---|---|---|
| 4.1 | **Create `.mcp.json`** (it does not exist, though CLAUDE.md cites it) registering the `nexus` stdio server per IDE | All three harnesses share one config |
| 4.2 | Register NEXUS MCP in the DSH profile `mcp-manager` (same pattern used for obscura) | DSH gets NEXUS tools natively |
| 4.3 | Add a Windows branch to `process_supervisor` (port probe via socket instead of `pgrep`/`systemctl`) | C8 |
| 4.4 | Single path-shim module reading `NEXUS_ROOT`, replacing `/home/roshan` reads | C7 without mass edits |
| 4.5 | Task Scheduler entry for `uvicorn server.unified:app` (replaces supervisord) | C6 |

### Phase 5 — JEV decision classifier (blocked on a key)

`~/.config/jev/jev.py` targets TypeSafe System One (`/v1/systemone`). **No API key is configured**, so model enumeration is impossible. Once a key exists:

1. `uv run python -c "import runpy"` → call `jev.py models` to enumerate.
2. Wire into `engine.py`'s `verifier=` seam for outcome verification — this replaces the lookup-table verifier the audits called "a table that teaches the system to lie".
3. Second target: the L1 short-circuit gate.

---

## 5. Tooling decisions (what boosts what)

| Tool | Replaces / adds | Boost | Cost |
|---|---|---|---|
| **uv** | pip + venv + pyenv + pipx | Venv in ~1 s, no admin, pins CPython | 40 MB |
| **fastembed + onnxruntime** | torch + sentence-transformers | 10× smaller, CPU-fast, index-compatible | 250 MB |
| **ruff + black** | ad-hoc linting | Catches the import-fatal class of bug | 30 MB |
| **pytest-xdist** | serial tests | Parallel suite on 8 threads | 5 MB |
| **`.mcp.json` + DSH profile row** | per-IDE drift | One server definition, three harnesses | 0 |
| obscura (already live) | web_fetch for JS sites | Cloudflare-proof research | done |
| VS Build Tools | — | Only if Rust becomes mandatory | **3.6 GB — rejected** |
| WSL2 distro | — | Linux tooling, TEI, systemd | 2 GB — **deferred** |
| Ollama | — | Local 7B-8B models | **Rejected** — 5.6 GB free RAM cannot host one alongside the IDE |

---

## 6. Recommended sequence and why

```
Phase 0 (unblock)   ──► Phase 1 (run)   ──► Phase 2 (embed)
                                              │
                          Phase 3 (data) ◄────┘
                                │
                          Phase 4 (wire)  ──► Phase 5 (JEV, needs key)
```

**Do Phase 0 + 1 + 2 as one block this session.** They are the only steps that convert a non-running repo into a measurable one. Phase 3 is data labour with no code risk and can follow incrementally. Phase 4 makes it usable from Claude Code / DSH / OpenCode. Phase 5 needs an external credential.

**Do not** tune V5 weights or enable cluster routing before Phase 3 — with empty clusters, edges and outcomes the flags are inert and any change is unattributable (measured: `cluster_definitions` is empty today).

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| OneDrive corrupts SQLite WAL | Phase 0.4 moves DB to D:; exclude `db/` from sync |
| 5.6 GB free RAM | `--workers 1`, no torch, close IDE during embed backfill |
| `requirements.txt` pulls torch transitively | Install Phase 1 deps explicitly, never `-r requirements.txt` until Phase 2 is proven |
| 78 hardcoded `/home/roshan` paths | Env shim (4.4); tests will keep failing until it lands |
| `iso_embedder.py` is import-fatal | Fix `sys`/`Any` imports as part of 2.3 |
| Windows Store python alias shadows real python | uv venv bypasses PATH entirely; keep the venv path explicit |
