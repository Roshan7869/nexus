# A07 — Artifact manifest in `server/execution_plane.py` (MODIFY companion to M06)

## GitNexus queries
```
context({name: "report_session"}) · query({search_query: "archive context project artifacts"})
```
Used: `read execution_plane.py:254-350` (report: normalize → record → belief_delta), `tool_session_broker.py:188` (`_archive_context`), OpenResearch `SKILL.md` orx-reports/orx-evidence modules + SYSTEM_PROMPT artifacts line, M06-doc.

## Research-backed context
- **OpenResearch orx-reports/orx-evidence (primary):** durable outputs (reports/figures/CSVs/PDFs) live in a project artifacts dir; evidence (logs/diffs/files/results) stays tied to the run that produced it. `orx logs <runId>` reads back by ID.
- **Cordis observational equivalence (§3.4):** recovery judged by indistinguishability under test-words — the manifest (paths + hashes) IS the test-word list for a session: same manifest ⇒ same evidence.
- **NEXUS RC6** (harness is a toolkit, not subsystem): manifest turns scattered context archives into a first-class execution record.

## Current state
`_archive_context` writes to `project_contexts`; `report_session` returns belief deltas but no file manifest. Artifacts path exists only as playbook text (A03).

## Blast radius: MEDIUM — additive keys on `report_session` return dict only; no existing key changes

## Detailed pseudocode
```python
ARTIFACTS_ROOT = Path(os.environ.get("NEXUS_ARTIFACTS_DIR", str(NEXUS_ROOT / "artifacts")))

def artifact_dir(session_id: str) -> Path:
    d = ARTIFACTS_ROOT / session_id; d.mkdir(parents=True, exist_ok=True); return d

def manifest(dir: Path) -> dict:
    # [{path, sha256, bytes}] sorted; the Cordis test-words for this session
    ...

def report_session(session_id, outcome, *, notes="", quality=1.0, skill_name=None, ide_session_id=None):
    ...existing resolve/normalize/record Heinrich...
    adir = artifact_dir(session_id)
    (adir / "summary.json").write_text(json.dumps({"outcome": o, "notes": notes, "quality": quality}))
    result = {...existing keys...,
              "artifacts_dir": str(adir),
              "artifact_manifest": manifest(adir)}   # NEW keys only
    return result
```

## Build steps
1. Add helpers + 2 keys; default dir under repo `artifacts/` (gitignored — add `artifacts/` to `.gitignore` in same commit).
2. Backfill: old sessions return `"artifacts_dir": None, "artifact_manifest": []` (no crash on missing dirs).
3. Playbook (A03) `{artifacts}` token resolves to per-session dir at open time.

## Tests
`test_manifest_keys_present`, `test_missing_dir_empty_manifest`, `test_manifest_sorted_hashes`.
