# F1 — Two-Tree Divergence · Release Engineering / SRE (Config Management)

**Severity:** HIGH · **Effort:** LOW · **Blast radius:** Systemic (parent of F2/F3/F4 + the false audit) · **Status:** OPEN

---

## 1. Symptom (observed)

The external "cruel audit" scored the whole platform at *5% reality* because it read a stale
copy of the code. Two `nexus` directories exist on the machine and have silently drifted:

- `/home/roshan/nexus` — branch `second-brain-v2`, **491 tracked files**, deep active history, **deployed**.
- `/home/roshan/Downloads/nexus` — branch `main`, **227 tracked files**, single "Initial commit", **~4 months stale**.

The stale tree still carries the legacy `server/nexus-mcp.py` and a `theater_backup/` directory
(the OKF graph engine). Auditing it produced conclusions that are false for production.

## 2. Root cause (mechanism)

Two **independently `git init`'d** repositories point at the **same remote**
(`github.com/Roshan7869/nexus.git`) but have **disjoint histories** — the smoking gun:

```
$ git -C /home/roshan/nexus            cat-file -t 9b2c08f  → fatal: Not a valid object name
$ git -C /home/roshan/Downloads/nexus  cat-file -t c8782ad  → fatal: Not a valid object name
# both have an "Initial commit" with DIFFERENT SHAs (083ff95 vs 9b2c08f)
```

Neither path is a symlink; each has its own `.git` object store, so commits never sync → unbounded
drift. There is **no single source of truth** enforced at the filesystem level.

**Why two trees exist (one-liner):** a downloaded/experimental snapshot in `Downloads/` was
re-initialised as its own repo instead of being reconciled with — or deleted in favour of — the
canonical working tree.

## 3. Scientific grounding

- **Single Source of Truth (SSOT) / version control as the system of record.** *Accelerate*
  (Forsgren, Humble, Kim 2018) shows via the DORA program that a single authoritative,
  version-controlled source correlates with lower change-fail rate and faster recovery. Two
  drifting SoTs is the anti-pattern that inflates both.
- **Reproducible / hermetic builds.** The Nix and Bazel literature (Dolstra 2006, *The Purely
  Functional Software Deployment Model*) argues a build must be a pure function of a *single*
  pinned input tree; ambiguous input trees make "what is deployed?" unanswerable — exactly the
  failure here.
- **GitOps declarative SoT** (Weaveworks 2017) — the desired state lives in *one* versioned repo;
  divergent working copies violate the invariant.
- **Continuous Delivery** (Humble & Farley 2010) §"Keep everything in version control" — untracked
  parallel trees are a deployment-risk anti-pattern.

## 4. Engineering context

The loaded MCP config is authoritative and already names the canonical tree — the drift is purely a
filesystem-hygiene problem, not a code problem:

```jsonc
// /home/roshan/nexus/.mcp.json  → "nexus" server
"command": "/home/roshan/nexus/venv/bin/python3",
"args":    ["/home/roshan/nexus/server/nexus-mcp-enhanced.py"],
"env":     { "NEXUS_DB_PATH": "/home/roshan/nexus/db/nexus.db", ... }
```

So `/home/roshan/nexus` is *both* git SoT and the deployed tree. The remediation is to **eliminate
the second SoT** and document the invariant, not to merge code.

## 5. Files / components to update

| Path | Current | Change | Annotation |
|------|---------|--------|------------|
| `/home/roshan/Downloads/nexus/` | Stale disjoint repo | Archive to tarball, then remove | N/A (removed) |
| `/home/roshan/nexus/docs/rca/00-INDEX.md` | — | Declares canonical tree (done) | doc-level |
| `/home/roshan/nexus/README.md` | — | Add "Canonical tree & SoT" note | prose |
| `/home/roshan/nexus/CONTRIBUTING.md` (create if absent) | — | "One SoT, never re-init in Downloads" rule | prose |

## 6. Execution steps

1. **Snapshot before delete (reversibility):**
   `tar czf ~/nexus-downloads-archive-$(date +%F).tgz -C /home/roshan/Downloads nexus`
2. Verify the archive is readable (`tar tzf …| head`).
3. Confirm no unique unpushed work in the stale tree:
   `git -C /home/roshan/Downloads/nexus status && git -C /home/roshan/Downloads/nexus log --oneline -5`
4. **Remove** the stale tree (after user confirmation — destructive):
   `rm -rf /home/roshan/Downloads/nexus`  *(HIGH-risk — see §8)*
5. Add the SoT invariant to `README.md` + `CONTRIBUTING.md`.

## 7. Verification (pass/fail)

- **PASS** iff `find /home/roshan -maxdepth 3 -name '.git' -path '*nexus*'` returns exactly one repo
  (the canonical tree), and the archive tarball exists and lists files.
- **PASS** iff `README.md` documents the canonical tree + entrypoint.

## 8. Risk & rollback

- Deleting a git repo is **destructive and irreversible** without the archive — step 1 is mandatory.
- Rollback: `tar xzf ~/nexus-downloads-archive-*.tgz -C /home/roshan/Downloads`.
- **Requires explicit user confirmation before `rm -rf`** (see safety guardrails). The archive-only
  variant (skip step 4) is the safe default if unsure.

## 9. References

1. Forsgren, N.; Humble, J.; Kim, G. *Accelerate: The Science of Lean Software and DevOps.* IT Revolution Press, 2018.
2. Humble, J.; Farley, D. *Continuous Delivery.* Addison-Wesley, 2010.
3. Dolstra, E. *The Purely Functional Software Deployment Model.* PhD thesis, Utrecht University, 2006.
4. Beyer, B. et al. *Site Reliability Engineering*, ch. "Release Engineering." O'Reilly, 2016.
5. Weaveworks. "GitOps: Operations by Pull Request." 2017.
