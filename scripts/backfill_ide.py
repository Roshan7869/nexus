"""RC7: One-time DETERMINISTIC backfill of historical ide='unknown' routing_log rows.

TELEMETRY/LABELING FIX (2026-06-27). server/router.py:_log_decision previously
hardcoded ide='unknown' for every route, so 4190/6145 (68%) of routing_log rows
carry ide='unknown'. The "claude_code barely wired (128 routes)" audit conclusion
was MNAR-invalid: it ignored the 4189-row unknown majority. This script asks
whether any of those unknown rows can be RELIABLY re-attributed to a real IDE.

ANTI-MNAR DISCIPLINE (Curnow 2024, BMC Med Res Methodol):
  Do NOT impute missing ide from predictors-of-missingness. A predictor-of-
  missingness is a variable that explains WHY ide is missing (i.e. which code
  path wrote the row) but is correlated with the outcome being measured —
  imputing from it amplifies MNAR bias. Curnovian discipline permits backfill
  ONLY from a DETERMINISTIC signal: a sibling column that records the SAME
  fact as ide (an entrypoint/session fingerprint), where the mapping is 1:1
  and verifiable, not statistical.

DETERMINISTIC FINGERPRINT CHECK:
  matched_model is the only candidate fingerprint. The known entrypoint
  fingerprints and their canonical ide tags are:
    'tool-attention-v4' -> 'opencode'   (server/tool_attention_v4.py)
    'intent-classifier' -> 'opencode'   (server/intent_classifier.py)
    'auto-router'       -> 'auto_invoke' (legacy; module removed)
  These fingerprints are deterministic: a row whose matched_model is one of
  these strings was written by exactly one entrypoint.

  BUT the ide='unknown' rows do NOT carry these fingerprints. Their
  matched_model values are REAL MODEL NAMES (kimi-k2, deepseek-v4-flash,
  qwen3-coder:480b, glm-5.1, ...), because router.py:_log_decision inserts
  result['model'] (the Thompson-sampled model), not an entrypoint tag.

  PROOF THAT matched_model IS A PREDICTOR-OF-MISSINGNESS, NOT DETERMINISTIC:
    matched_model='kimi-k2' appears in BOTH ide='unknown' (1119 rows) AND
    ide='claude-code' (35 rows). The same model is written by multiple
    entrypoints (router.py unknown path AND nexus-mcp.py claude-code path).
    Imputing ide from matched_model would therefore misattribute claude-code
    routes as unknown (or vice versa) — textbook MNAR amplification.

  CONCLUSION: No airtight deterministic signal exists for the unknown rows.
  Per Curnow (2024), we LEAVE them as 'unknown' honestly rather than amplify
  bias. This script therefore WRITES NOTHING on the unknown majority. It only
  corrects rows where a deterministic fingerprint is present AND mislabeled
  (none found in the current DB, but the check is retained for safety).

  routing_log has NO sibling session_id/entrypoint_id column, so no other
  deterministic path exists.

SAFETY:
  - --dry-run by DEFAULT. Writes only when --apply is passed.
  - Idempotent: re-running produces the same result (deterministic UPDATEs
    only; no row is touched twice).
  - Does NOT rebuild the live table, does NOT ALTER schema, does NOT touch
    the running MCP processes.

Usage:
  python3 scripts/backfill_ide.py             # dry-run (default): print distribution
  python3 scripts/backfill_ide.py --apply     # apply deterministic corrections only
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "db" / "nexus.db"

# Deterministic entrypoint fingerprints -> canonical ide tag.
# A row carrying one of these matched_model values was written by exactly one
# entrypoint, so re-attributing its ide is a deterministic reconstruction
# (not statistical imputation). This is Curnow-safe.
FINGERPRINT_TO_IDE: dict[str, str] = {
    "tool-attention-v4": "opencode",
    "intent-classifier": "opencode",
    "auto-router": "auto_invoke",
}


def _distribution(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    return conn.execute(
        "SELECT ide, COUNT(*) FROM routing_log GROUP BY ide ORDER BY 2 DESC"
    ).fetchall()


def main(apply: bool = False) -> int:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    before = _distribution(conn)
    total = sum(n for _, n in before)
    print("=" * 64)
    print(f"routing_log ide distribution (BEFORE) — {total} rows")
    print("=" * 64)
    for ide, n in before:
        print(f"  {ide:14s} {n:6d}  ({100*n/max(total,1):5.1f}%)")

    # ── Deterministic mislabel check ──────────────────────────────────────
    # Find rows whose matched_model is a known entrypoint fingerprint but
    # whose ide does NOT match the canonical tag. These are the ONLY rows
    # safe to backfill (deterministic reconstruction from a sibling column
    # that records the same fact).
    print("\n" + "=" * 64)
    print("DETERMINISTIC FINGERPRINT MISLABEL SCAN (Curnow-safe)")
    print("=" * 64)
    candidates: list[tuple[int, str]] = []
    for fingerprint, canonical_ide in FINGERPRINT_TO_IDE.items():
        rows = conn.execute(
            "SELECT id FROM routing_log WHERE matched_model = ? AND ide != ?",
            (fingerprint, canonical_ide),
        ).fetchall()
        print(f"  fingerprint '{fingerprint}' -> '{canonical_ide}': {len(rows)} mislabeled rows")
        for r in rows:
            candidates.append((r["id"], canonical_ide))

    print(f"\n  deterministic backfill candidates: {len(candidates)}")

    # ── The unknown majority: NO deterministic signal ─────────────────────
    unknown_rows = conn.execute(
        "SELECT COUNT(*) FROM routing_log WHERE ide = 'unknown'"
    ).fetchone()[0]
    print("\n" + "=" * 64)
    print(f"ide='unknown' rows: {unknown_rows}")
    print("=" * 64)
    # Evidence that matched_model is a predictor-of-missingness, not a
    # deterministic signal: the same model name appears under multiple ide
    # tags. Print the top overlap as evidence.
    overlap = conn.execute(
        """
        SELECT matched_model,
               SUM(CASE WHEN ide='unknown' THEN 1 ELSE 0 END) AS n_unknown,
               SUM(CASE WHEN ide!='unknown' THEN 1 ELSE 0 END) AS n_known
        FROM routing_log
        WHERE matched_model NOT IN ('tool-attention-v4','intent-classifier','auto-router')
          AND matched_model != 'unknown'
        GROUP BY matched_model
        HAVING n_unknown > 0 AND n_known > 0
        ORDER BY n_unknown DESC
        LIMIT 5
        """
    ).fetchall()
    if overlap:
        print("  EVIDENCE matched_model is a predictor-of-missingness, NOT deterministic:")
        print("  (same model name written by both unknown and known-ide entrypoints)")
        for r in overlap:
            print(f"    matched_model='{r['matched_model']}': "
                  f"unknown={r['n_unknown']}  known-ide={r['n_known']}")
    print("  -> No deterministic signal. LEAVING unknown rows as 'unknown' (Curnow 2024).")
    print("  -> Imputing from matched_model/project would amplify MNAR bias.")

    # ── Apply (only deterministic fingerprint corrections) ────────────────
    applied = 0
    if apply and candidates:
        conn.execute("BEGIN")
        for row_id, canonical_ide in candidates:
            conn.execute(
                "UPDATE routing_log SET ide = ? WHERE id = ? AND ide != ?",
                (canonical_ide, row_id, canonical_ide),
            )
            applied += 1
        conn.commit()
        print(f"\nAPPLIED: {applied} deterministic fingerprint corrections")
    elif apply and not candidates:
        print("\nAPPLY: nothing to write — no deterministic mislabels found "
              "(unknown majority left as-is per Curnow).")
    else:
        print(f"\nDRY-RUN: would write {len(candidates)} deterministic corrections. "
              "Unknown majority left untouched regardless.")

    after = _distribution(conn)
    print("\n" + "=" * 64)
    print(f"routing_log ide distribution (AFTER {'APPLY' if apply else 'DRY-RUN'})")
    print("=" * 64)
    for ide, n in after:
        print(f"  {ide:14s} {n:6d}  ({100*n/max(total,1):5.1f}%)")

    conn.close()
    return 0


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    if not apply:
        print("NOTE: running in --dry-run mode (default). "
              "Pass --apply to write deterministic corrections only.")
    sys.exit(main(apply=apply))