"""
Verb-Intent Classifier — maps task verbs + context to skill names.

Sits as Step 3.5 in route_task pipeline.
Provides learning loop via outcome logging + weight updates.
"""
from server.db import get_db_path as _resolve_db_path
import sqlite3, json, re, time, hashlib, logging
from pathlib import Path
from collections import defaultdict
from typing import Optional

DB_PATH = _resolve_db_path()

logger = logging.getLogger(__name__)


def _metric_fail(op: str, exc: Exception) -> None:
    """Fail-loud seam for swallowed learning-signal writes (G13, atom A5_feedback).

    A silent ``except: pass`` on a ``routing_log`` write makes the row vanish from
    *both* sides of the coverage ratio (rl_feedback_loop.py:74 reads routing_log),
    so the loop cannot see its own dropped signal (Reflexion arXiv:2303.11366).

    Today this emits a greppable, structured WARNING (``NEXUS_WRITE_FAIL``) — a
    log-scrapeable metric with no new schema. G12 may upgrade this single seam to a
    durable counter sink WITHOUT touching the call sites below.
    """
    logger.warning("NEXUS_WRITE_FAIL op=%s err=%s", op, exc)

# ── Stop words (filtered from verb extraction) ──
STOP_WORDS = {
    "a", "an", "the", "in", "on", "at", "to", "for", "with", "by",
    "and", "or", "of", "is", "it", "its", "this", "that", "from",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "can", "could", "shall", "should", "may", "might",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "just", "about", "into", "over", "after",
    "before", "between", "under", "again", "further", "then", "once",
    "here", "there", "above", "below", "up", "down", "out", "off",
    "any", "my", "your", "our", "their", "his", "her", "itself",
    "myself", "yourself", "new", "use", "using", "used",
}

# ── Verb→Skill map ──
# Each entry: verb → [(skill_name, base_confidence)]
# Context boosts: verb_contexts[verb] = {phrase: [(skill, boost)]}
VERB_SKILL_MAP = {
    "fix": [("investigate", 0.75), ("security-review", 0.50)],
    "debug": [("investigate", 0.85)],
    "investigate": [("investigate", 0.90)],
    "find": [("investigate", 0.70)],
    "write": [("article-writing", 0.70), ("content-engine", 0.60), ("document-release", 0.35), ("api-design", 0.20)],
    "deploy": [("ship", 0.70), ("land-and-deploy", 0.50)],
    "setup": [("setup-deploy", 0.50), ("setup-gbrain", 0.40), ("deployment-patterns", 0.30)],
    "configure": [("setup-deploy", 0.50), ("deployment-patterns", 0.30)],
    "review": [("review", 0.65), ("github-code-review", 0.50), ("plan-design-review", 0.30), ("plan-devex-review", 0.20)],
    "restore": [("context-restore", 0.80)],
    "save": [("context-save", 0.60), ("checkpoint", 0.60)],
    "freeze": [("freeze", 0.85)],
    "unfreeze": [("unfreeze", 0.85)],
    "audit": [("design-review", 0.35), ("skill-stocktake", 0.35), ("cso", 0.30), ("security-scan", 0.30)],
    "benchmark": [("benchmark", 0.80)],
    "compare": [("benchmark-models", 0.80)],
    "brainstorm": [("office-hours", 0.70)],
    "check": [("health", 0.70)],
    "scrape": [("scrape", 0.70), ("skillify", 0.40)],
    "sync": [("sync-gbrain", 0.70)],
    "upgrade": [("gstack-upgrade", 0.80)],
    "monitor": [("canary", 0.70)],
    "test": [("devex-review", 0.40), ("qa", 0.30)],
    "qa": [("qa", 0.75), ("qa-only", 0.50)],
    "run": [("qa", 0.20), ("qa-only", 0.15), ("autoplan", 0.15)],
    "orchestrate": [("swarm-orchestration", 0.65), ("swarm-advanced", 0.40)],
    "chain": [("stream-chain", 0.70)],
    "compact": [("strategic-compact", 0.80)],
    "sparc": [("sparc-methodology", 0.80)],
    "pair": [("pair-agent", 0.80)],
    "generate": [("design-shotgun", 0.60)],
    "migrate": [("database-migrations", 0.60), ("postgres-patterns", 0.40)],
    "update": [("document-release", 0.70)],
    "get": [("claude", 0.40), ("codex", 0.40)],
    "search": [("search-first", 0.50), ("iterative-retrieval", 0.45)],
    "create": [("api-design", 0.40), ("skill-builder", 0.30), ("design-consultation", 0.20), ("design-html", 0.20)],
    "refactor": [("plan-eng-review", 0.55), ("investigate", 0.30)],
    "build": [("adaptive-imagining-cat", 0.30), ("plan-eng-review", 0.25), ("frontend-ui-engineering", 0.20)],
    "implement": [("tdd-workflow", 0.25), ("api-design", 0.20), ("security-review", 0.15), ("postgres-patterns", 0.15)],
    "design": [("design-consultation", 0.50), ("design-html", 0.40), ("plan-eng-review", 0.20)],
    "design:plan": [("plan-design-review", 0.70)],
}

# ── Context boosts: disambiguate based on domain nouns ──
VERB_CONTEXT = {
    "write": {
        "blog": [("article-writing", 0.25), ("content-engine", 0.20)],
        "article": [("article-writing", 0.25), ("content-engine", 0.15)],
        "post": [("content-engine", 0.20), ("article-writing", 0.15)],
        "documentation": [("document-release", 0.40)],
        "docs": [("document-release", 0.40)],
        "api": [("api-design", 0.30), ("document-release", 0.20)],
        "newsletter": [("content-engine", 0.25)],
    },
    "create": {
        "api": [("api-design", 0.50), ("backend-api", 0.30)],
        "endpoint": [("api-design", 0.50)],
        "design": [("design-consultation", 0.40), ("design-html", 0.30)],
        "system": [("design-consultation", 0.30)],
        "skill": [("skill-builder", 0.50)],
        "dashboard": [("frontend-ui-engineering", 0.35)],
        "pipeline": [("adaptive-imagining-cat", 0.30)],
        "ml": [("adaptive-imagining-cat", 0.30)],
        "machine": [("adaptive-imagining-cat", 0.30)],
        "cms": [("adaptive-imagining-cat", 0.25), ("api-design", 0.25)],
        "content": [("adaptive-imagining-cat", 0.25)],
    },
    "build": {
        "dashboard": [("frontend-ui-engineering", 0.40), ("design-html", 0.30)],
        "trading": [("frontend-ui-engineering", 0.30), ("design-html", 0.25)],
        "chart": [("frontend-ui-engineering", 0.35)],
        "payment": [("security-review", 0.40), ("api-design", 0.30)],
        "cache": [("plan-eng-review", 0.40)],
        "distributed": [("plan-eng-review", 0.40)],
        "recommendation": [("adaptive-imagining-cat", 0.40)],
        "editor": [("plan-eng-review", 0.30), ("adaptive-imagining-cat", 0.30)],
        "real-time": [("plan-eng-review", 0.25)],
        "saas": [("plan-eng-review", 0.30), ("plan-ceo-review", 0.25)],
        "platform": [("plan-eng-review", 0.25), ("plan-ceo-review", 0.20)],
    },
    "implement": {
        "tdd": [("tdd-workflow", 0.60), ("e2e-testing", 0.30)],
        "test-driven": [("tdd-workflow", 0.60)],
        "postgres": [("postgres-patterns", 0.55)],
        "clickhouse": [("clickhouse-io", 0.60)],
        "microservice": [("plan-eng-review", 0.30)],
        "oauth": [("security-review", 0.45), ("api-design", 0.30)],
        "encryption": [("security-review", 0.45), ("api-design", 0.20)],
        "ab": [("benchmark", 0.30), ("adaptive-imagining-cat", 0.25)],
        "testing": [("benchmark", 0.25)],
        "pipeline": [("adaptive-imagining-cat", 0.25)],
    },
    "debug": {
        "memory": [("frontend-ui-engineering", 0.30)],
        "login": [("security-review", 0.25)],
        "auth": [("security-review", 0.25)],
        "token": [("security-review", 0.25)],
        "cors": [("api-design", 0.30)],
        "api": [("api-design", 0.20)],
        "websocket": [("api-design", 0.25)],
        "connection": [("api-design", 0.15)],
        "crash": [],
        "stack": [],
        "trace": [],
    },
    "fix": {
        "bug": [("investigate", 0.15)],
        "login": [("security-review", 0.30)],
        "auth": [("security-review", 0.30)],
    },
    "find": {
        "root": [("investigate", 0.20)],
        "cause": [("investigate", 0.15)],
        "db": [("postgres-patterns", 0.25)],
        "database": [("postgres-patterns", 0.25)],
        "deploy": [("deployment-patterns", 0.25)],
        "inconsistency": [("api-design", 0.20)],
        "stuck": [("investigate", 0.10)],
    },
    "review": {
        "pr": [("review", 0.25), ("github-code-review", 0.25)],
        "pull": [("review", 0.25), ("github-code-review", 0.25)],
        "request": [("review", 0.15)],
        "design": [("plan-design-review", 0.45)],
        "devex": [("plan-devex-review", 0.45)],
        "developer": [("plan-devex-review", 0.40)],
    },
    "setup": {
        "deploy": [("setup-deploy", 0.40)],
        "deployment": [("setup-deploy", 0.35)],
        "gbrain": [("setup-gbrain", 0.50)],
        "ci": [("deployment-patterns", 0.35), ("setup-deploy", 0.25)],
        "cd": [("deployment-patterns", 0.30)],
        "browser": [("setup-browser-cookies", 0.35), ("browse", 0.30)],
        "monitoring": [("deployment-patterns", 0.35)],
    },
    "audit": {
        "visual": [("design-review", 0.50)],
        "design": [("design-review", 0.45)],
        "skill": [("skill-stocktake", 0.45)],
        "skills": [("skill-stocktake", 0.45)],
        "claude": [("skill-stocktake", 0.35)],
        "security": [("cso", 0.40), ("security-scan", 0.30)],
        "pentest": [("cso", 0.40)],
        "owasp": [("cso", 0.40)],
    },
    "run": {
        "qa": [("qa", 0.50), ("qa-only", 0.30)],
        "review": [("autoplan", 0.40), ("review", 0.30)],
        "reviews": [("autoplan", 0.40)],
        "auto": [("autoplan", 0.30)],
        "security": [("security-scan", 0.20)],
        "sparc": [("sparc-methodology", 0.70)],
        "migration": [("database-migrations", 0.50)],
        "migrate": [("database-migrations", 0.50)],
    },
    "implement:plan": {
        "review": [("plan-eng-review", 0.45)],
    },
}

# ── Phrase-level patterns (multi-word intent signals) ──
PHRASE_INTENTS = [
    (["root", "cause", "analysis"], [("investigate", 0.75)]),
    (["500", "error"], [("investigate", 0.50)]),
    (["race", "condition"], [("investigate", 0.55)]),
    (["memory", "leak"], [("investigate", 0.55), ("frontend-ui-engineering", 0.30)]),
    (["data", "inconsistency"], [("investigate", 0.40), ("api-design", 0.25)]),
    (["stack", "trace"], [("investigate", 0.70)]),
    (["pull", "request"], [("review", 0.55), ("github-code-review", 0.45)]),
    (["test-driven", "development"], [("tdd-workflow", 0.70)]),
    (["blog", "post"], [("article-writing", 0.50), ("content-engine", 0.40)]),
    (["design", "system"], [("design-consultation", 0.60), ("design-html", 0.30)]),
    (["design", "plan"], [("plan-design-review", 0.65)]),
    (["deploy", "production"], [("ship", 0.60), ("land-and-deploy", 0.40)]),
    (["ceo", "review"], [("plan-ceo-review", 0.55), ("gstack-openclaw-ceo-review", 0.30)]),
    (["strategy", "review"], [("plan-ceo-review", 0.55)]),
    (["engineering", "review"], [("plan-eng-review", 0.60)]),
    (["database", "migration"], [("database-migrations", 0.55)]),
    (["sparc", "methodology"], [("sparc-methodology", 0.75)]),
    (["browser", "automation"], [("browse", 0.45), ("setup-browser-cookies", 0.30)]),
    (["prometheus", "grafana"], [("deployment-patterns", 0.45)]),
    (["setup", "deploy"], [("setup-deploy", 0.60)]),
    (["ci", "cd"], [("deployment-patterns", 0.45), ("setup-deploy", 0.30)]),
    (["deploy", "fail"], [("investigate", 0.55), ("deployment-patterns", 0.35)]),
    (["deploy", "failed"], [("investigate", 0.55), ("deployment-patterns", 0.35)]),
    (["developer", "experience"], [("devex-review", 0.50)]),
    (["maximum", "safety"], [("guard", 0.55), ("careful", 0.40)]),
    (["production", "database"], [("careful", 0.35), ("guard", 0.35)]),
    (["delete", "production"], [("careful", 0.55), ("guard", 0.50)]),
    (["force", "push"], [("careful", 0.60), ("guard", 0.50)]),
    (["rm", "rf"], [("careful", 0.70), ("guard", 0.55)]),
    (["ab", "testing"], [("benchmark", 0.35), ("adaptive-imagining-cat", 0.20)]),
    (["a/b", "testing"], [("benchmark", 0.35)]),
    (["save", "state"], [("context-save", 0.55), ("checkpoint", 0.50)]),
    (["save", "checkpoint"], [("checkpoint", 0.70)]),
    (["save", "progress"], [("context-save", 0.55)]),
    (["restore", "state"], [("context-restore", 0.70)]),
    (["save", "context"], [("context-save", 0.60)]),
    (["restore", "context"], [("context-restore", 0.70)]),
    (["weekly", "retro"], [("retro", 0.60)]),
    (["engineering", "retro"], [("retro", 0.55)]),
    (["code", "quality"], [("health", 0.50)]),
    (["health", "check"], [("health", 0.60)]),
    (["health", "score"], [("health", 0.60)]),
    (["page", "load"], [("benchmark", 0.35)]),
    (["model", "compare"], [("benchmark-models", 0.55)]),
    (["cross-model"], [("benchmark-models", 0.60)]),
    (["deploy", "canary"], [("canary", 0.60)]),
    (["canary", "check"], [("canary", 0.65)]),
    (["post-deploy"], [("canary", 0.50)]),
    (["single", "agent"], [("swarm-advanced", 0.20)]),
    (["multi", "agent"], [("swarm-orchestration", 0.45)]),
    (["chat", "encryption"], [("security-review", 0.40)]),
    (["end-to-end", "encryption"], [("security-review", 0.45)]),
    (["oauth", "pkce"], [("security-review", 0.45), ("api-design", 0.30)]),
    (["zero", "downtime"], [("deployment-patterns", 0.30)]),
]


def extract_verb(task: str) -> str | None:
    """Extract the primary action verb from a task query."""
    words = task.lower().split()
    # Handle two-word verbs: "set up" → "setup"
    if len(words) >= 2 and words[0] == "set" and words[1] == "up":
        return "setup"
    for w in words:
        w_clean = w.strip(".,!?;:'\"()[]{}")
        if w_clean and w_clean not in STOP_WORDS:
            return w_clean
    return None


def extract_context(task: str) -> list[str]:
    """Extract domain context words from task (nouns after the verb)."""
    words = task.lower().split()
    context = []
    found_verb = False
    for w in words:
        w_clean = w.strip(".,!?;:'\"()[]{}")
        if not w_clean or w_clean in STOP_WORDS:
            continue
        if not found_verb:
            found_verb = True
            continue
        if len(w_clean) >= 2:
            context.append(w_clean)
    return context


def match_phrases(task: str) -> list[tuple[str, float]]:
    """Check if any known phrases appear in the task (order-independent)."""
    task_lower = task.lower()
    matches = []
    for phrase_words, skills in PHRASE_INTENTS:
        # Check if ALL words in the phrase appear in the task
        if all(pw in task_lower for pw in phrase_words):
            for skill, conf in skills:
                matches.append((skill, conf))
    return matches


# ── Learned weight cache ──
_LEARNED_WEIGHTS = None  # {verb: {skill: adjustment}}

def _load_learned_weights():
    """Load learned weight adjustments from DB into module cache."""
    global _LEARNED_WEIGHTS
    _LEARNED_WEIGHTS = {}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT verb, skill_name, adjustment FROM intent_weights WHERE ABS(adjustment) > 0.01"
        ).fetchall()
        for row in rows:
            _LEARNED_WEIGHTS.setdefault(row["verb"], {})[row["skill_name"]] = row["adjustment"]
        conn.close()
    except Exception:
        pass


def predict(task: str) -> list[dict]:
    """Predict skills for a task based on verb-intent + context.
    
    Returns list of {name, score, source, category} sorted by score descending.
    """
    task_lower = task.lower()
    verb = extract_verb(task)
    context = extract_context(task)
    
    # Load learned weights on first call
    if _LEARNED_WEIGHTS is None:
        _load_learned_weights()
    
    # Step 1: Phrase matching (highest priority)
    phrase_hits = match_phrases(task)
    
    # Step 2: Verb matching
    verb_hits = []
    if verb and verb in VERB_SKILL_MAP:
        for skill, base_conf in VERB_SKILL_MAP[verb]:
            verb_hits.append((skill, base_conf))
        
        # Context boosts
        if verb in VERB_CONTEXT:
            for ctx_word in context:
                if ctx_word in VERB_CONTEXT[verb]:
                    for skill, boost in VERB_CONTEXT[verb][ctx_word]:
                        idx = next((i for i, (s, _) in enumerate(verb_hits) if s == skill), None)
                        if idx is not None:
                            verb_hits[idx] = (skill, min(verb_hits[idx][1] + boost, 0.95))
                        else:
                            verb_hits.append((skill, boost))
    
    # Step 3: Apply learned weight adjustments from outcome history
    if verb and _LEARNED_WEIGHTS and verb in _LEARNED_WEIGHTS:
        for skill, adj in _LEARNED_WEIGHTS[verb].items():
            idx = next((i for i, (s, _) in enumerate(verb_hits) if s == skill), None)
            if idx is not None:
                verb_hits[idx] = (skill, min(max(verb_hits[idx][1] + adj, 0.0), 0.95))
            else:
                verb_hits.append((skill, max(adj, 0.0)))
    
    # Step 4: Check for "plan:" prefixed verbs (e.g., "design:plan" for design-plan review)
    if verb:
        plan_verb = f"{verb}:plan"
        if plan_verb in VERB_SKILL_MAP:
            for skill, base_conf in VERB_SKILL_MAP[plan_verb]:
                verb_hits.append((skill, base_conf))
            if plan_verb in VERB_CONTEXT:
                for ctx_word in context:
                    if ctx_word in VERB_CONTEXT[plan_verb]:
                        for skill, boost in VERB_CONTEXT[plan_verb][ctx_word]:
                            idx = next((i for i, (s, _) in enumerate(verb_hits) if s == skill), None)
                            if idx is not None:
                                verb_hits[idx] = (skill, min(verb_hits[idx][1] + boost, 0.95))
    
    # Step 5: Special handling for security/destructive intent
    destructive_words = ["delete", "drop", "remove", "kill", "destroy", "force", "bypass"]
    if any(dw in task_lower for dw in destructive_words):
        verb_hits.append(("careful", 0.40))
        verb_hits.append(("guard", 0.30))
    
    # Step 6: Merge phrase hits (they take priority)
    merged = {}
    for skill, conf in phrase_hits:
        merged[skill] = max(merged.get(skill, 0), conf)
    for skill, conf in verb_hits:
        merged[skill] = max(merged.get(skill, 0), conf)
    
    # Build result list
    results = [
        {"name": skill, "score": round(conf, 3), "source": "intent", "category": "INVOKE" if conf >= 0.55 else "REVIEW"}
        for skill, conf in sorted(merged.items(), key=lambda x: -x[1])[:10]
    ]
    
    return results


def log_intent_telemetry(task: str, predicted_skills: list[str], actual_outcome: str):
    """Intent-classifier routing telemetry (not the MCP report_outcome tool).

    Terminal outcomes delegate entirely to learning.record_outcome (one path).
    Non-terminal 'routed' writes routing_log only — no resource counter inflation.
    """
    skills = predicted_skills or []

    if actual_outcome in ("correct", "wrong", "partial") and skills:
        try:
            from server.learning import record_outcome
            record_outcome(
                skills[0],
                actual_outcome,
                task_summary=task[:200],
                project="nexus",
                ide="opencode",
                co_skills=skills[1:] or None,
            )
        except Exception as e:
            logger.warning("intent record_outcome failed: %s", e)
            _metric_fail("record_outcome", e)
        if actual_outcome == "correct":
            try:
                conn = sqlite3.connect(str(DB_PATH))
                verb = extract_verb(task)
                if verb:
                    conn.execute("""
                        INSERT INTO intent_learning (verb, correct_skills, count, timestamp)
                        VALUES (?, ?, 1, datetime('now'))
                    """, (verb, json.dumps(skills[:3])))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning("intent_learning insert failed: %s", e)
                _metric_fail("intent_learning", e)
        return

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            INSERT INTO routing_log (task_summary, project, matched_tier, matched_model,
                                     matched_skills, outcome, skill_name, ide, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            task[:200], "nexus", 2, "intent-classifier",
            json.dumps(skills[:5]),
            actual_outcome,
            skills[0] if skills else None,
            "opencode",
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("routing_log insert failed: %s", e)
        _metric_fail("routing_log", e)


def update_weights(verb: str, correct_skills: list[str]):
    """Update VERB_SKILL_MAP weights based on successful outcomes.
    
    Currently logged to DB for analysis; future: auto-update map.
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            INSERT INTO intent_learning (verb, correct_skills, timestamp)
            VALUES (?, ?, datetime('now'))
        """, (verb, json.dumps(correct_skills)))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("update_weights insert failed: %s", e)
        _metric_fail("update_weights", e)


def ensure_schema():
    """Ensure learning tables exist."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS intent_learning (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                verb TEXT NOT NULL,
                correct_skills TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 1,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Table for learned verb weight adjustments
        conn.execute("""
            CREATE TABLE IF NOT EXISTS intent_weights (
                verb TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                adjustment REAL NOT NULL DEFAULT 0.0,
                sample_count INTEGER NOT NULL DEFAULT 1,
                last_updated TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (verb, skill_name)
            )
        """)
        conn.commit()
        conn.close()
    except Exception:
        pass


def recalibrate():
    """Recalibrate verb→skill weights from outcome history.
    
    Reads routing_log for intent-classifier entries with known outcomes,
    computes empirical success rates per verb+skill, and stores adjustments
    in intent_weights table for use by predict().
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT task_summary, matched_skills, outcome, skill_name
            FROM routing_log
            WHERE matched_model = 'intent-classifier'
              AND outcome IN ('correct', 'wrong')
              AND task_summary IS NOT NULL
        """).fetchall()
        
        # Group by verb → {skill: {correct, total}}
        from collections import defaultdict
        stats = defaultdict(lambda: defaultdict(lambda: {"correct": 0, "total": 0}))
        
        for row in rows:
            verb = extract_verb(row["task_summary"])
            if not verb:
                continue
            skills = json.loads(row["matched_skills"]) if row["matched_skills"] else []
            outcome = row["outcome"]
            for skill in skills:
                if outcome == "correct":
                    stats[verb][skill]["correct"] += 1
                stats[verb][skill]["total"] += 1
        
        # Compute adjustments and store
        for verb, skill_stats in stats.items():
            for skill, counts in skill_stats.items():
                if counts["total"] < 3:
                    continue  # Minimum sample threshold
                emp_rate = counts["correct"] / max(counts["total"], 1)
                # Base rate from VERB_SKILL_MAP
                base = 0.0
                if verb in VERB_SKILL_MAP:
                    for s, c in VERB_SKILL_MAP[verb]:
                        if s == skill:
                            base = c
                            break
                adjustment = emp_rate * 0.7 - base * 0.3  # Blend: lean toward empirical
                conn.execute("""
                    INSERT INTO intent_weights (verb, skill_name, adjustment, sample_count, last_updated)
                    VALUES (?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(verb, skill_name) DO UPDATE SET
                        adjustment = excluded.adjustment,
                        sample_count = excluded.sample_count,
                        last_updated = datetime('now')
                """, (verb, skill, round(adjustment, 4), counts["total"]))
        
        conn.commit()
        conn.close()
        return len(rows)
    except Exception as e:
        logger.warning(f"Recalibrate failed: {e}")
        return 0


ensure_schema()

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'recalibrate':
        n = recalibrate()
        print(f"Recalibrated: reviewed {n} log entries")
        _load_learned_weights()
        print(f"Loaded {sum(len(v) for v in (_LEARNED_WEIGHTS or {}).values())} learned weight adjustments")
    elif len(sys.argv) > 1:
        task = ' '.join(sys.argv[1:])
        results = predict(task)
        print(f"Task: {task}")
        for r in results:
            print(f"  {r['name']} (score={r['score']}, cat={r['category']})")
