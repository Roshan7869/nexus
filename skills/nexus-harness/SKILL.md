---
name: nexus-harness
description: Open, report, and reconcile IDE-local NEXUS harness sessions with evidence manifests.
---

# nexus-harness

1. `local_harness_open(task, project, budget, ide, task_type="general")` → session + `visible_tools` + `realm` + `site_status` (URL → `bound` or failure reason).
   - `task_type` selects the project's per-task sites from `config/site_registry.yaml` (`projects.<id>.tasks.<task_type>.sites`). With no task entry, the project-wide sites are used.
   - Sites are harvested on open. A site that fails to harvest does not block the session; it is reported in `site_status`.
2. Work with listed tools only; artifacts go under the session dir.
3. `local_harness_report(session_id, outcome, notes)` → returns belief deltas, `artifact_manifest`, and `sites_released` (URLs whose harvested content was removed on close).
4. `local_harness_reconcile(desired_json)` → read-only diff (apply lands Wave 2).

Rules: completed sessions are immutable — new ideas get new sessions. Harvested site content lives only for the session; closing it removes it.
