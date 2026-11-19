# W6 — Wenlan retirement (complete)

**Date:** 2026-11-19 · **Decision:** the user uninstalled Wenlan; the mirror is retired.
**Outcome:** Obsidian is the sole surviving mirror. Nothing in NEXUS can call Wenlan.

## What was removed

| Location | Change |
|---|---|
| `%LOCALAPPDATA%\hermes\config.yaml` | deleted the `mcp_servers.wenlan` block (it pointed at the now-missing `wenlan-mcp.exe`; it was already `enabled: false`) |
| `~/.dsh/profiles/desktop/cordis.patch.yml` | deleted the `mcp-manager.servers.wenlan` block (stdio, 5 lines) |
| `scripts/refresh_nexus.py` | stage 6 no longer shells out to `sync_wenlan.py`; it returns an explicit `retired:` line instead of failing the pipeline |
| `scripts/sync_wenlan.py` | header marked RETIRED (kept as reference implementation) |
| `server/integrations/wenlan_mcp.py` | header marked RETIRED (no importers) |
| `scripts/bridge_obsidian_wenlan.py` | header marks the Wenlan half RETIRED |
| `D:\nexus-data\wenlan_sync.json` | already absent (no incremental state to clean) |

The three retired modules are **kept, not deleted**: they document the working
memory contract (`capture` is the primitive that `recall` can see; `write_page` is
invisible to it) and would be needed again if the app were reinstalled.

## Preserved data (needs a user decision)

`%LOCALAPPDATA%\wenlan` — **614 MB**, 4,630 of 5,066 resources captured before the
uninstall (436 were pending, 2 of them permanently rejected as `credential_leak`).
This is the user's data and was **not deleted**. Options:

- keep as an archive (recommended; costs 614 MB and nothing else),
- export to Obsidian as a `Wenlan-legacy/` folder, then delete,
- delete outright.

## Verification

- `wenlan-mcp.exe` not resolvable on PATH; binary directory contains only `uninstall.exe`.
- No `mcp_servers.wenlan` entry remains in the Hermes config.
- No `wenlan:` entry remains in the DSH mcp-manager profile.
- `refresh_nexus.py` no longer depends on any Wenlan binary, so the W5 scheduled
  refresh cannot fail on it.

## Why this mattered for W5

`refresh_nexus.py` called `sync_wenlan.py` as stage 6. With the app uninstalled that
stage would have failed on **every** scheduled run, turning the weekly refresh into a
permanently red job. Removing the coupling was a prerequisite for scheduling it.
