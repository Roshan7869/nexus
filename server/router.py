#!/usr/bin/env python3
"""~/nexus/server/router.py — Unified v4 Routing Pipeline

Pipeline (all steps, OFF by default, each gated by feature flag):
  1. auto_trigger.detect_cluster(task)        → activated clusters
  2. cluster_router.route_to_clusters(task)    → filtered resource IDs
  3. tool_attention_v4 ISO scoring              → ranked candidates
  4. precondition.check()                        → filter by state gates
  5. hallucination.gate()                        → reject hallucinated calls
  6. Thompson Sampling (learner)                 → best model for task_type
  7. Fallback: keyword tier matching             → existing TIERS/SPECIALTY

When all features OFF: same behavior as v1 keyword routing.
When cluster_routing ON: Steps 1-5 activate.
When routing (Thompson) ON: Step 6 overrides tier model selection.

Papers: ToolkenGPT (2310.03054), Reflexion (2303.11366), SWE-agent (2405.15793)
"""
from server.db import get_db_path as _resolve_db_path
import sys, json, sqlite3, os, logging, time, hashlib, math, re, random
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)
try:
    from server.service_layer import bundle_for_cluster
except Exception:
    from service_layer import bundle_for_cluster

try:
    from server.intent_classifier import predict as intent_predict, log_intent_telemetry as intent_log
except Exception:
    intent_predict = None
    intent_log = None

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = _resolve_db_path()

from traffic_class import classify_traffic  # noqa: E402

# ── Feature Flags — imported from centralized config ──
# Resolution: YAML > env var > code default (false).
from core.config import get_learning_config
_LEARN_CFG = get_learning_config()
CLUSTER_ROUTING = _LEARN_CFG.is_cluster_routing()
THOMPSON_ROUTING = _LEARN_CFG.is_thompson_routing()
REFLECTION_ENABLED = _LEARN_CFG.is_reflection_enabled()

# ── Harness Research — academic context augmentation ──────────────────
HARNESS_RESEARCH_ENABLED = os.environ.get(
    "NEXUS_HARNESS_RESEARCH", "true"
).lower() != "false"

RESEARCH_KEYWORDS = re.compile(
    r'\b(?:research|paper|papers|arxiv|study|survey|literature|academic|'
    r'semantic.scholar|papers.with.code|'
    r'scientific|journal|conference|citation|'
    r'state.of.the.art|bibliography|peer.review|'
    r'open.source)\b', re.IGNORECASE
)

# ── Tier Definitions (fallback when Thompson Sampling off) ──────────────
TIERS = {
    1: {
        "name": "T1 Fast",
        "keywords": [
            "rename", "format", "lint", "indent", "comment",
            "add log", "console.log", "typo", "fix spacing",
            "add semicolon", "sort imports", "simple test",
            "update version", "bump", "add type annotation"
        ],
        "anti_keywords": ["architecture", "design", "refactor", "scale"],
        "model": "gemini-3-flash-preview:cloud",
        "token_limit": 512
    },
    2: {
        "name": "T2 Standard",
        "keywords": [
            "implement", "feature", "fix bug", "build", "create",
            "add", "update", "refactor", "optimize", "component",
            "endpoint", "api", "form", "validation", "test",
            "middleware", "hook", "state", "props", "migration",
            "query", "schema", "page", "route", "auth"
        ],
        "anti_keywords": ["design system", "architecture decision", "entire codebase"],
        "model": "devstral-small-2:24b-cloud",
        "token_limit": 2048
    },
    3: {
        "name": "T3 Complex",
        "keywords": [
            "architecture", "design system", "refactor entire",
            "distributed", "scale", "concurrent", "race condition",
            "security audit", "compliance", "payment", "transaction",
            "machine learning", "pipeline", "infrastructure",
            "multi-tenant", "real-time sync", "event sourcing",
            "cqrs", "microservice", "orchestration"
        ],
        "anti_keywords": [],
        "model": "qwen3-coder:480b-cloud",
        "token_limit": 0
    }
}

SPECIALTY = {
    "vision": {
        "keywords": ["screenshot", "image", "ui review", "visual", "design review",
                     "photo", "icon", "color scheme", "layout", "mockup"],
        "model": "qwen3-vl:235b-cloud",
        "token_limit": 2048
    },
    "longctx": {
        "keywords": ["entire codebase", "all files", "whole repo", "full codebase",
                     "audit every", "across all", "codebase-wide"],
        "model": "deepseek-v4-flash:cloud",
        "token_limit": 4096
    },
    "reasoning": {
        "keywords": ["prove", "proof", "theorem", "formal", "verify logically",
                     "hard problem", "impossible", "paradox"],
        "model": "cogito-2.1:671b-cloud",
        "token_limit": 0
    },
    "architecture": {
        "keywords": ["architecture decision", "system design", "ADR",
                     "design doc", "tech spec", "architectural"],
        "model": "mistral-large-3:675b-cloud",
        "token_limit": 4096
    }
}


# ═══════════════════════════════════════════════════════════════════════
# Step 0: Task Classification (TaskClassifier from learner.py)
# ═══════════════════════════════════════════════════════════════════════

def classify_task(task: str) -> tuple:
    """Classify task type. Returns (task_type, confidence, cluster)."""
    # Use NexusLearner if Thompson routing enabled
    if THOMPSON_ROUTING:
        try:
            sys.path.insert(0, str(Path.home() / 'nexus'))
            from core.learner import NexusLearner
            learner = NexusLearner()
            task_type, confidence = learner.classify_task(task)
            learner.close()
            return task_type, confidence, None
        except Exception as e:
            logger.warning(f"Thompson classifier failed: {e}")

    # Fallback: keyword-based classification (from auto_trigger patterns)
    task_lower = task.lower()
    type_signals = {
        "coding": ["code", "implement", "fix", "bug", "function", "class", "method", "debug", "refactor", "compile"],
        "reasoning": ["analyze", "explain", "why", "compare", "evaluate", "assess", "reason", "logic", "prove", "deduce"],
        "agentic": ["run", "execute", "deploy", "ship", "build", "automate", "pipeline", "workflow"],
        "architecture": ["architect", "design", "system", "pattern", "ddd", "microservice", "spec"],
        "devops": ["deploy", "docker", "ci/cd", "release", "infrastructure", "kubernetes"],
        "frontend": ["ui", "frontend", "component", "css", "react", "dashboard", "page", "layout"],
        "research": ["research", "paper", "arxiv", "study", "survey", "find", "search"],
    }
    scores = {t: 0 for t in type_signals}
    for t, keywords in type_signals.items():
        for kw in keywords:
            if kw in task_lower:
                scores[t] += 1
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "general", 0.3, None
    return best, min(scores[best] / 3.0, 1.0), None


# ═══════════════════════════════════════════════════════════════════════
# Step 1: Auto-Trigger (cluster auto-detection)
# ═══════════════════════════════════════════════════════════════════════

def detect_cluster(task: str) -> Optional[Dict]:
    """Auto-trigger: detect which cluster(s) should activate.
    Returns: {cluster_name, affinity, default_skill, default_agents} or None."""
    if not CLUSTER_ROUTING:
        return None
    try:
        sys.path.insert(0, str(Path.home() / 'nexus/server'))
        from auto_trigger import AutoTrigger
        trigger = AutoTrigger()
        results = trigger.detect_signal(task)  # returns list of dicts
        if results:
            # Use the top-ranked cluster
            top = results[0]
            result = {
                "cluster": top.get("cluster"),
                "affinity": top.get("affinity", 0),
                "default_skill": top.get("default_skill"),
                "default_agents": top.get("default_agents", []),
                "matched_signals": top.get("matched_signals", []),
            }
            # Log activation
            try:
                conn2 = sqlite3.connect(str(DB_PATH), timeout=30)
                conn2.execute("PRAGMA journal_mode=WAL")
                conn2.execute("PRAGMA busy_timeout=15000")
                conn2.execute(
                    "INSERT INTO cluster_activation_log(query,project,activated_clusters,affinity_scores,total_resources_scanned,latency_ms) VALUES (?,?,?,?,?,?)",
                    (task[:200], "nexus",
                     json.dumps([result.get("cluster", "unknown")]),
                     json.dumps({"affinity": result.get("affinity", 0)}),
                     100, 0.0)
                )
                conn2.commit()
                conn2.close()
            except Exception:
                pass
            return result
        return None
    except Exception as e:
        logger.warning(f"Auto-trigger failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# Step 2: Cluster Router (centroid-based affinity)
# ═══════════════════════════════════════════════════════════════════════

def route_to_clusters(task: str, detected_cluster: str = None) -> Optional[Dict]:
    """Route task to domain clusters. Returns {clusters, resource_ids} or None.
    Uses cluster_membership (single source of truth) for resource filtering.
    
    When a specific cluster is detected, includes resources from that cluster
    PLUS resources from the 'general' cluster (low-confidence fallback pool).
    Falls back to resources.cluster column if cluster_membership lookup fails.
    """
    if detected_cluster:
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=15000")
            # P0-2: Read from cluster_membership (authoritative) with JOIN
            rows = conn.execute("""
                SELECT DISTINCT r.id FROM resources r
                JOIN cluster_membership cm ON cm.resource_id = r.id
                WHERE (cm.cluster_name = ? OR cm.cluster_name = 'general')
                  AND r.status = 'active'
            """, (detected_cluster,)).fetchall()
            conn.close()
            resource_ids = [r[0] for r in rows]
            if resource_ids:
                return {"clusters": [detected_cluster, "general"], "resource_ids": resource_ids}
        except Exception as e:
            logger.warning(f"DB cluster_membership filter failed, falling back to column: {e}")
            # Fallback: legacy resources.cluster column
            try:
                conn = sqlite3.connect(str(DB_PATH), timeout=30)
                rows = conn.execute(
                    "SELECT id FROM resources WHERE (cluster=? OR cluster='general') AND status='active'",
                    (detected_cluster,)
                ).fetchall()
                conn.close()
                resource_ids = [r[0] for r in rows]
                if resource_ids:
                    return {"clusters": [detected_cluster, "general"], "resource_ids": resource_ids}
            except Exception as e2:
                logger.warning(f"Fallback cluster filter also failed: {e2}")
    return None


# ═══════════════════════════════════════════════════════════════════════
# Step 3: Tool Attention v4 (ISO scoring)
# ═══════════════════════════════════════════════════════════════════════

def iso_score_candidates(task: str, filtered_ids: List[int] = None) -> List[Dict]:
    """Score candidates using Tool Attention v4 ISO algorithm.
    If filtered_ids provided, only score those resources (post-cluster filter).
    Returns empty list if sentence_transformers is not installed."""
    try:
        # Quick check: can we import the heavy dependency?
        import importlib.util
        spec = importlib.util.find_spec('sentence_transformers')
        if spec is None:
            logger.info("ISO scoring skipped: sentence_transformers not installed")
            return []
    except (ImportError, ModuleNotFoundError):
        logger.info("ISO scoring skipped: sentence_transformers not importable")
        return []
    try:
        sys.path.insert(0, str(Path.home() / 'nexus/server'))
        from tool_attention_v4 import TA_V4
        scores = TA_V4.intent_router.compute_iso_scores(task)

        if filtered_ids:
            scores = [s for s in scores if s.get("id") in filtered_ids]

        return scores[:20]
    except Exception as e:
        logger.warning(f"ISO scoring failed: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════
# Step 4-5: Precondition + Hallucination gates
# ═══════════════════════════════════════════════════════════════════════

def check_preconditions(candidates: List[Dict]) -> List[Dict]:
    """Filter candidates by precondition state gates."""
    if not CLUSTER_ROUTING:
        return candidates
    try:
        sys.path.insert(0, str(Path.home() / 'nexus/server'))
        from precondition import check_precondition
        filtered = []
        for c in candidates:
            if check_precondition(c.get("name", "")):
                filtered.append(c)
        return filtered if filtered else candidates  # Don't empty the list
    except Exception:
        return candidates

def hallucination_gate(candidates: List[Dict], active_set: List[str]) -> List[Dict]:
    """Reject tool calls not in the active set."""
    if not CLUSTER_ROUTING or not active_set:
        return candidates
    try:
        sys.path.insert(0, str(Path.home() / 'nexus/server'))
        from hallucination import HallucinationGate
        gate = HallucinationGate()
        filtered = [c for c in candidates if gate.validate(c.get("name", ""), active_set)]
        return filtered if filtered else candidates
    except Exception:
        return candidates


# ═══════════════════════════════════════════════════════════════════════
# Step 6: Thompson Sampling Model Selection
# ═══════════════════════════════════════════════════════════════════════

def thompson_sample_model(task_type: str, candidates: List[Dict]) -> Optional[Dict]:
    """Sample from Beta beliefs to find best model for task_type.
    Requires NEXUS_THOMPSON_ROUTING=true and minimum 50 samples."""
    if not THOMPSON_ROUTING:
        return None
    try:
        sys.path.insert(0, str(Path.home() / 'nexus'))
        from core.learner import NexusLearner
        learner = NexusLearner()
        if not learner.can_route(task_type):
            logger.info(f"Thompson: insufficient samples for {task_type}, using fallback")
            learner.close()
            return None
        result = learner.thompson_sample(candidates, task_type)
        learner.close()
        return result
    except Exception as e:
        logger.warning(f"Thompson sampling failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# Skill Matching (existing, unchanged)
# ═══════════════════════════════════════════════════════════════════════

def score_tier(task: str, tier_config: dict) -> int:
    task_lower = task.lower()
    score = 0
    for kw in tier_config["keywords"]:
        if kw.lower() in task_lower:
            score += 1
    for ak in tier_config["anti_keywords"]:
        if ak.lower() in task_lower:
            score -= 3
    return score

# ── Phase 3: Outcome Evaluation ──
_EVAL_DATASET = None

def _load_eval_dataset() -> dict:
    """Load eval dataset for outcome evaluation."""
    global _EVAL_DATASET
    if _EVAL_DATASET is not None:
        return _EVAL_DATASET
    try:
        eval_path = Path.home() / 'nexus/server/scripts/faiss_eval_dataset.json'
        if eval_path.exists():
            with open(eval_path) as f:
                data = json.load(f)
            _EVAL_DATASET = {}
            for entry in data:
                if entry.get('expected_resource_names'):
                    _EVAL_DATASET[entry['task_text'].lower().strip()] = entry['expected_resource_names']
    except Exception:
        _EVAL_DATASET = {}
    return _EVAL_DATASET

def _evaluate_outcome(task: str, returned_skills: list) -> str:
    """Evaluate routing outcome by comparing against ground truth.
    
    Returns: 'correct', 'wrong' (eval dataset has ground truth), or 'routed'
    (no ground truth available — neutral, does NOT update Thompson beliefs;
    per server/implicit_outcomes.py design).
    """
    task_lower = task.lower().strip()
    eval_data = _load_eval_dataset()
    
    # Check eval dataset first
    if task_lower in eval_data:
        expected = eval_data[task_lower]
        if set(returned_skills) & set(expected):
            return 'correct'
        return 'wrong'
    
    # No ground truth available — honest neutral state, no signal to update
    # Thompson beliefs. 'routed' (NOT 'partial') is the IRC-sanctioned neutral
    # value per server/implicit_outcomes.py — it prevents a flood of fake
    # 'partial' signals from biasing Thompson Sampling for tasks outside the
    # 56-entry eval dataset.
    return 'routed'

def find_matching_skills(task: str, project: str = "default",
                          filtered_ids: List[int] = None,
                          preferred_names: List[str] = None,
                          max_results: int = 5) -> list:
    """Find matching skills. If filtered_ids provided, only search those resources."""
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row

    task_words = set(task.lower().split())
    task_lower = task.lower()

    # UCB1: Fetch total routing log size for exploration bonus calculation
    try:
        N_total = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0] or 1
    except Exception:
        N_total = 1

    # Phase P4: Pre-compute cluster sizes for diversity-weighted exploration
    cluster_sizes = {}
    for r in conn.execute("""
        SELECT cm.cluster_name, COUNT(DISTINCT cm.resource_id) as cnt 
        FROM cluster_membership cm
        JOIN resources r ON r.id = cm.resource_id AND r.status = 'active'
        GROUP BY cm.cluster_name
    """).fetchall():
        cluster_sizes[r['cluster_name']] = r['cnt']
    max_cluster_size = max(cluster_sizes.values()) if cluster_sizes else 1

    # Fetch active/recent reflection fixes and failures
    fixes_map = {}
    recent_failures = set()
    try:
        fixes_rows = conn.execute("""
            SELECT resource_id, fix_description, times_successful 
            FROM reflection_fixes 
            WHERE times_applied > 0
        """).fetchall()
        for f in fixes_rows:
            rid = f["resource_id"]
            desc = f["fix_description"] or ""
            if desc.startswith("Use ") and desc.endswith(" instead"):
                alt_name = desc[4:-8]
                if rid not in fixes_map:
                    fixes_map[rid] = []
                fixes_map[rid].append((alt_name, f["times_successful"] or 0))
                
        fail_rows = conn.execute("""
            SELECT resource_name FROM reflection_failures 
            WHERE fix_successful IS NULL OR fix_successful = 0
        """).fetchall()
        for fr in fail_rows:
            if fr["resource_name"]:
                recent_failures.add(fr["resource_name"].lower())
    except Exception as e:
        logger.warning(f"Failed to fetch reflection failures or fixes: {e}")

    # Phase C: Two-pass approach
    # Pass 1: Keyword-based SQL search to find relevant skills regardless of usage
    # Pass 2: Usage-based search for proven skills
    # Merge both passes

    # Build LIKE clauses for each significant task word
    STOP_WORDS = frozenset(('the', 'and', 'for', 'with', 'this', 'that', 'from', 'have', 'been', 'will', 'your', 'what', 'how', 'when', 'where', 'why', 'can', 'not', 'but', 'are', 'was', 'were', 'been', 'being', 'has', 'had', 'does', 'did', 'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just', 'don', 'now', 'about', 'into', 'over', 'after', 'before', 'between', 'under', 'again', 'further', 'then', 'once', 'here', 'there', 'above', 'below', 'any', 'up', 'down', 'out', 'off', 'in', 'on', 'at', 'to', 'of', 'a', 'an', 'is', 'it', 'its', 'he', 'she', 'they', 'them', 'his', 'her', 'our', 'my', 'you', 'we', 'new', 'use', 'using', 'used'))
    significant_words = sorted(
        [w for w in task_words if len(w) >= 3 and w not in STOP_WORDS],
        key=len, reverse=True
    )[:5]  # Limit to 5 most significant (longest) words

    if filtered_ids:
        placeholders = ",".join("?" for _ in filtered_ids)
        id_filter = f"AND id IN ({placeholders})"
        id_params = filtered_ids
    else:
        id_filter = ""
        id_params = []

    # Pass 1: Keyword search — single UNION ALL query instead of N per-word queries
    keyword_skills = []
    if significant_words:
        union_parts = []
        params = []
        for word in significant_words:
            base_like = f'%{word}%'
            union_parts.append(f"""
                SELECT name, description, body_text, resource_type,
                       times_triggered, times_correct, times_wrong, id,
                       cluster
                FROM resources
                WHERE status='active'
                  AND (name LIKE ? OR description LIKE ? OR body_text LIKE ?)
                  {id_filter}
            """)
            params.extend([base_like, base_like, base_like] + list(id_params))

        query = " UNION ALL ".join(union_parts)
        rows = conn.execute(query, params).fetchall()
        for row in rows:
            keyword_skills.append(row)

    # Deduplicate by name
    seen_names = set()
    unique_keyword_skills = []
    for row in keyword_skills:
        if row['name'] not in seen_names:
            seen_names.add(row['name'])
            unique_keyword_skills.append(row)

    # Pass 2: Usage-based search for proven skills
    usage_skills = conn.execute(f"""
        SELECT name, description, body_text, resource_type,
               times_triggered, times_correct, times_wrong, id,
               cluster
        FROM resources
        WHERE status='active' AND times_correct > 0
        {id_filter}
        ORDER BY times_correct DESC, name ASC
        LIMIT 200
    """, id_params).fetchall()

    # Merge: keyword matches first, then usage-based
    all_rows = unique_keyword_skills + [r for r in usage_skills if r['name'] not in seen_names]

    # Simple stemming function
    def stem(w):
        for suffix in ('ing', 'tion', 'sion', 'ment', 'ness', 'ed', 'es', 's'):
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                return w[:-len(suffix)]
        return w

    task_stems = {stem(tw) for tw in task_words if len(tw) >= 3}

    matches = []
    preferred_names = preferred_names or []
    preferred_name_set = {name.lower() for name in preferred_names}

    # Phase P1: Name normalization — strip prefixes so prefixed skills match queries
    NAME_PREFIXES = ('claude-skill-', 'hermes-skill-', 'gstack-', 'claude-cmd-', 'opencode-skill-', 'gbrain-skill-')
    def strip_prefix(name):
        for prefix in NAME_PREFIXES:
            if name.startswith(prefix):
                return name[len(prefix):]
        return name

    for row in all_rows:
        skill_name_lower = (row['name'] or '').lower()
        normalized_name = strip_prefix(skill_name_lower)
        desc_lower = (row['description'] or '').lower()
        body_lower = ((row['body_text'] or '')[:1000]).lower()
        skill_text = f"{normalized_name} {desc_lower} {body_lower}"
        skill_words = set(skill_text.split())

        # Word overlap
        overlap = len(task_words & skill_words)

        # Stem-based fuzzy matching
        fuzzy_hit = any(
            ts in normalized_name or ts in desc_lower
            for ts in task_stems
        )

        # Phase C: Phrase matching — check multi-word phrases from task
        phrase_bonus = 0.0
        words = task_lower.split()
        for i in range(len(words) - 1):
            phrase = f"{words[i]} {words[i+1]}"
            if phrase in normalized_name or phrase in desc_lower:
                phrase_bonus += 0.15
        phrase_bonus = min(phrase_bonus, 0.3)

        # Phase C: Exact name match boost — HIGHEST priority
        name_match_bonus = 0.0
        for tw in task_words:
            if len(tw) >= 3 and tw in normalized_name:
                name_match_bonus += 0.3
            elif len(tw) >= 4 and tw in desc_lower:
                name_match_bonus += 0.1
        name_match_bonus = min(name_match_bonus, 0.6)

        # Phase C: Body text search bonus
        body_bonus = 0.0
        for tw in significant_words:
            if tw in body_lower:
                body_bonus += 0.05
        body_bonus = min(body_bonus, 0.3)

        # Phase P1: Normalization bonus — prefixed skills match by clean name
        normalization_bonus = 0.0
        if normalized_name != skill_name_lower:
            for tw in task_words:
                if len(tw) >= 3 and tw in normalized_name:
                    normalization_bonus += 0.2
                    if tw == normalized_name:
                        normalization_bonus += 0.2
        normalization_bonus = min(normalization_bonus, 0.4)

        if overlap >= 1 or fuzzy_hit or phrase_bonus > 0 or name_match_bonus > 0 or body_bonus > 0 or normalization_bonus > 0:
            total_triggered = row['times_triggered'] or 0
            total_correct = row['times_correct'] or 0
            success_rate = (total_correct / total_triggered * 100) if total_triggered > 0 else 100

            # Phase C: Enhanced scoring
            raw_overlap = min(overlap * 0.10, 0.4)
            fuzzy_bonus = 0.12 if fuzzy_hit and overlap == 0 else 0.0
            # UCB1 exploration bonus: flat for cold (n=0), formula for low-use (1-10)
            n_i = total_triggered
            if n_i == 0:
                exploration_bonus = 0.15  # flat cold-start bonus
            elif n_i <= 10:
                exploration_bonus = 0.15 * math.sqrt(math.log(max(N_total, 2)) / max(n_i, 1))
            else:
                exploration_bonus = 0.0  # Thompson Sampling sufficient
            # Phase P2: Body richness bonus — skills with rich body get extra exploration
            body_len = len(row['body_text'] or '')
            richness_bonus = min(body_len / 50000, 0.1) if total_triggered == 0 else 0.0
            # Phase P4: Cluster diversity bonus — under-represented clusters get extra exploration
            cluster = row['cluster'] or '' if 'cluster' in row.keys() else ''
            cluster_diversity_bonus = 0.0
            if total_triggered == 0:
                if not cluster or cluster not in cluster_sizes:
                    cluster_diversity_bonus = 0.10
                else:
                    size = cluster_sizes.get(cluster, max_cluster_size)
                    cluster_diversity_bonus = min((max_cluster_size / size) * 0.03, 0.10)
            # Phase 4: Cluster size imbalance penalty (ISSUE 8)
            imbalance_penalty = 0.0
            if cluster and cluster in cluster_sizes:
                imbalance_penalty = (cluster_sizes[cluster] / max_cluster_size) * 0.05
            
            # Phase 4: Reflection penalty & boost
            reflection_penalty = 0.0
            reflection_boost = 0.0
            if row['name'].lower() in recent_failures:
                reflection_penalty = 0.30
            for failed_rid, alts in fixes_map.items():
                for alt_name, success_count in alts:
                    if alt_name.lower() == row['name'].lower():
                        reflection_boost = max(reflection_boost, 0.15 + min(success_count * 0.05, 0.15))
            
            score = raw_overlap + fuzzy_bonus + phrase_bonus + name_match_bonus + body_bonus + normalization_bonus + min(success_rate / 300, 0.25) + exploration_bonus + richness_bonus + cluster_diversity_bonus - imbalance_penalty - reflection_penalty + reflection_boost
            category = "INVOKE" if score >= 0.55 else "REVIEW" if score >= 0.3 else "SKIP"

            matches.append({
                "name": row['name'],
                "score": round(score, 3),
                "category": category,
                "success_rate": round(success_rate, 1),
                "type": row['resource_type'],
                "resource_id": row['id']
            })

    # Gap 3: fuse co_triggered / similar_to edge signal into ranking (small boost)
    matches = _apply_edge_graph_boost(conn, matches)

    if preferred_names:
        placeholders = ",".join("?" for _ in preferred_names)
        preferred_rows = conn.execute(f"""
            SELECT name, description, resource_type, times_triggered, times_correct, id
            FROM resources
            WHERE name IN ({placeholders}) AND status='active'
        """, preferred_names).fetchall()
        for rank, row in enumerate(preferred_rows):
            if any(m["name"] == row["name"] for m in matches):
                continue
            total_triggered = row["times_triggered"] or 0
            total_correct = row["times_correct"] or 0
            success_rate = (total_correct / total_triggered * 100) if total_triggered > 0 else 100
            score = 0.97 - (rank * 0.03)
            matches.append({
                "name": row["name"],
                "score": round(score, 3),
                "category": "INVOKE",
                "success_rate": round(success_rate, 1),
                "type": row["resource_type"],
                "resource_id": row["id"],
                "service_bundle": True,
            })

    if len(matches) < 5 and not filtered_ids:
        try:
            sys.path.insert(0, str(Path.home() / 'nexus/server'))
            from tool_attention_v4 import ToolAttentionV4
            v4 = ToolAttentionV4.get()
            candidates = v4.intent_router.compute_iso_scores(task)
            for c in candidates[:10]:
                if any(m.get("name") == c["name"] for m in matches):
                    continue
                total = c.get("times_triggered", 0) or 0
                correct = c.get("times_correct", 0) or 0
                success_rate = (correct / total * 100) if total > 0 else 100
                matches.append({
                    "name": c["name"],
                    "score": round(0.6 + min(c["iso_score"], 0.4), 3),
                    "category": "REVIEW",
                    "success_rate": round(success_rate, 1),
                    "type": c["resource_type"]
                })
        except Exception:
            pass

    # ── Epsilon-Greedy Cold-Start Exploration ──────────────────
    import random
    try:
        # Constant 25% exploration rate (temporarily high to cover 827 cold resources;
        # decay back to 15% after coverage improves — Thompson Sampling handles exploitation)
        if random.random() < 0.25:
            cluster_param = None
            if filtered_ids:
                # P0-2 fix: Use cluster_membership (authoritative) not resources.cluster
                row_c = conn.execute("""
                    SELECT cm.cluster_name FROM cluster_membership cm
                    JOIN resources r ON r.id = cm.resource_id
                    WHERE cm.resource_id IN ({placeholders})
                    AND r.status = 'active'
                    ORDER BY cm.affinity DESC LIMIT 1
                """.format(placeholders=','.join('?' for _ in filtered_ids[:5])),
                    filtered_ids[:5]
                ).fetchone()
                if row_c:
                    cluster_param = row_c["cluster_name"]
                    
            if cluster_param:
                # P0-2 fix: JOIN cluster_membership for authoritative cluster lookup
                cold_rows = conn.execute("""
                    SELECT r.id, r.name, r.resource_type FROM resources r
                    JOIN cluster_membership cm ON cm.resource_id = r.id
                    WHERE r.status = 'active' AND r.times_triggered = 0 AND cm.cluster_name = ?
                    ORDER BY RANDOM() LIMIT 5
                """, (cluster_param,)).fetchall()
            else:
                cold_rows = conn.execute("""
                    SELECT id, name, resource_type FROM resources 
                    WHERE status = 'active' AND times_triggered = 0 
                    ORDER BY RANDOM() LIMIT 5
                """).fetchall()
                
            for cr in cold_rows:
                # Inject with high score and review category
                matches.insert(0, {
                    "name": cr["name"],
                    "score": 0.95,
                    "category": "REVIEW",
                    "success_rate": 100.0,
                    "type": cr["resource_type"],
                    "resource_id": cr["id"],
                    "_explore": True
                })
    except Exception as e:
        logger.warning(f"Epsilon-greedy exploration injection failed: {e}")

    # Dynamic Z-score filtering
    non_explore_scores = [m["score"] for m in matches if not m.get("_explore")]
    if len(non_explore_scores) >= 3:
        mean = sum(non_explore_scores) / len(non_explore_scores)
        variance = sum((x - mean) ** 2 for x in non_explore_scores) / len(non_explore_scores)
        std = variance ** 0.5 + 1e-10
        filtered_matches = []
        for m in matches:
            if m.get("_explore"):
                filtered_matches.append(m)
            else:
                z = (m["score"] - mean) / std
                if z >= -1.0 or m["score"] > 0.70:
                    filtered_matches.append(m)
        matches = filtered_matches

    matches.sort(
        key=lambda m: (
            0 if m.get("_explore") else 1, # Boost exploration candidates to the absolute top
            0 if m.get("name", "").lower() in preferred_name_set else 1,
            -m["score"],
            m["name"],
        )
    )
    
    # Retrieve adjacent edges for top results
    for m in matches[:max_results]:
        if "resource_id" in m:
            try:
                edges = conn.execute("""
                    SELECT e.target_id, e.edge_type, r.name as target_name 
                    FROM resource_edges e
                    JOIN resources r ON e.target_id = r.id
                    WHERE e.source_id = ?
                """, (m["resource_id"],)).fetchall()
                if edges:
                    m["edges"] = [dict(e) for e in edges]
            except Exception as e:
                logger.warning(f"Failed to query adjacent resource edges: {e}")

    conn.close()
    return matches[:max_results]


def _apply_edge_graph_boost(conn, matches: List[Dict]) -> List[Dict]:
    """Gap 3: add a small score boost from learned graph edges.

    co_triggered edges with co_trigger_count>0 are the primary signal (live
    multi-skill outcomes). similar_to provides a weaker content prior.
    Boost is capped so graph never dominates keyword/Thompson signal.
    """
    if not matches:
        return matches
    ids = [m.get("resource_id") for m in matches if m.get("resource_id") is not None]
    if not ids:
        return matches
    try:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""
            SELECT rid,
                   MAX(CASE WHEN edge_type='co_triggered' THEN weight ELSE 0 END) AS co_w,
                   SUM(CASE WHEN edge_type='co_triggered'
                            THEN COALESCE(co_trigger_count,0) ELSE 0 END) AS co_n,
                   MAX(CASE WHEN edge_type='similar_to' THEN weight ELSE 0 END) AS sim_w
            FROM (
                SELECT source_id AS rid, edge_type, weight, co_trigger_count
                FROM resource_edges
                WHERE source_id IN ({placeholders})
                  AND edge_type IN ('co_triggered','similar_to')
                UNION ALL
                SELECT target_id AS rid, edge_type, weight, co_trigger_count
                FROM resource_edges
                WHERE target_id IN ({placeholders})
                  AND edge_type IN ('co_triggered','similar_to')
            )
            GROUP BY rid
            """,
            ids + ids,
        ).fetchall()
        by_id = {r["rid"] if isinstance(r, sqlite3.Row) else r[0]: r for r in rows}
    except Exception as e:
        logger.warning(f"edge graph boost query failed: {e}")
        return matches

    for m in matches:
        rid = m.get("resource_id")
        if rid is None or rid not in by_id:
            continue
        r = by_id[rid]
        if isinstance(r, sqlite3.Row):
            co_w = float(r["co_w"] or 0)
            co_n = float(r["co_n"] or 0)
            sim_w = float(r["sim_w"] or 0)
        else:
            co_w, co_n, sim_w = float(r[1] or 0), float(r[2] or 0), float(r[3] or 0)
        co_boost = 0.0
        if co_n > 0:
            co_boost = min(0.10, 0.04 * co_w + 0.02 * math.log1p(co_n))
        sim_boost = min(0.04, 0.03 * sim_w) if sim_w > 0 else 0.0
        boost = co_boost + sim_boost
        if boost <= 0:
            continue
        m["score"] = round(float(m["score"]) + boost, 3)
        m["graph_boost"] = round(boost, 3)
        m["category"] = (
            "INVOKE" if m["score"] >= 0.55 else "REVIEW" if m["score"] >= 0.3 else "SKIP"
        )
    return matches


def _get_cold_resources(top_k: int = 3) -> List[Dict]:
    """Return never-triggered active resources, sorted by content quality proxy.

    The section count acts as a rough proxy for content depth. Resources with
    more sections tend to have richer descriptions and are more likely to
    produce useful outcomes when first explored.
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT r.name, r.resource_type, COUNT(rs.id) as section_count
            FROM resources r
            JOIN resource_sections rs ON r.id = rs.resource_id
            WHERE r.times_triggered = 0 AND r.status = 'active'
            GROUP BY r.id
            ORDER BY section_count DESC
            LIMIT ?
        """, (top_k,)).fetchall()
        conn.close()
        return [{"name": r["name"], "resource_type": r["resource_type"],
                 "section_count": r["section_count"]} for r in rows]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════
# UNIFIED ROUTING PIPELINE
# ═══════════════════════════════════════════════════════════════════════

def route_task(task: str, project: str = "default", use_embedding: bool = False,
               ide: str = None, write_feedback: bool = True) -> dict:
    """Unified v4 routing pipeline.

    Pipeline flow:
      0. Classify task type
      1. Auto-trigger → detect cluster
      2. Cluster route → filter resource IDs
      3. ISO score → rank candidates
      4. Precondition → filter by state
      5. Hallucination gate → validate
      6. Thompson Sampling → pick model
      7. Fallback → keyword tier + specialty matching

    ``ide`` labels the calling entrypoint for telemetry (OTel-style
    telemetry.sdk.* attribution): 'claude-code' (MCP), 'http' (HTTP daemon),
    'opencode' (tool-attention-v4), 'auto_invoke', or 'unknown' when truly
    undeterminable. Override via the ``NEXUS_IDE`` env var forces any
    entrypoint. RC7 (2026-06-27): previously hardcoded 'unknown' here, which
    mislabeled 68% of routing_log rows and invalidated the claude_code-wiring
    audit (MNAR). Threading the tag is a labeling fix, not a wiring fix.

    ``write_feedback`` (P1 pure-rank): when False, ranking only — no
    ``_log_decision`` / ``record_outcome`` side effects. Used by v5 L1 intent
    so hierarchical rerank does not credit the wrong skill or double-count.
    """
    if ide is None:
        ide = os.environ.get("NEXUS_IDE", "unknown")
    start_time = time.time()

    # Step 0: Classify
    task_type, confidence, _ = classify_task(task)
    pipeline_steps = ["classify"]

    # Step 1: Auto-trigger
    trigger_result = detect_cluster(task)
    detected_cluster = None
    default_skill = None
    default_agents = []
    preferred_names = []
    if trigger_result:
        detected_cluster = trigger_result.get("cluster")
        default_skill = trigger_result.get("default_skill")
        default_agents = trigger_result.get("default_agents", [])
        preferred_names = bundle_for_cluster(detected_cluster)
        pipeline_steps.append("auto_trigger")

    # Step 2: Cluster route
    cluster_result = route_to_clusters(task, detected_cluster)
    filtered_ids = None
    if cluster_result:
        filtered_ids = cluster_result.get("resource_ids", [])
        if not detected_cluster:
            detected_cluster = cluster_result.get("clusters", ["unknown"])[0] if cluster_result.get("clusters") else None
        pipeline_steps.append("cluster_route")

    # Step 3: ISO scoring (Tool Attention v4) — skip when use_embedding=False
    iso_candidates = []
    if use_embedding:
        iso_candidates = iso_score_candidates(task, filtered_ids)
        if iso_candidates:
            pipeline_steps.append("iso_scoring")

    # Steps 4-5: Precondition + Hallucination gates
    if iso_candidates:
        active_names = [c.get("name", "") for c in iso_candidates[:10]]
        iso_candidates = check_preconditions(iso_candidates)
        iso_candidates = hallucination_gate(iso_candidates, active_names)
        if CLUSTER_ROUTING:
            pipeline_steps.append("gates")

    # Step 3.7 (P3): Harness is planned at the end via attach_harness_to_result.
    # Network research is NOT run inside ranking; paper titles must never become skills.
    harness_results = []  # legacy name kept empty; artifacts attach separately

    # Step 3.5: Verb-Intent Classification (handles task-type routing)
    intent_results = []
    if intent_predict:
        try:
            intent_results = intent_predict(task)
            if intent_results:
                pipeline_steps.append("intent_classifier")
                intent_skills = [r["name"] for r in intent_results]
                # Intent telemetry only when write_feedback; pure rank skips.
                if write_feedback:
                    outcome = _evaluate_outcome(task, intent_skills)
                    try:
                        intent_log(task, intent_skills, outcome)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Intent classifier failed: {e}")

    # Step 6: Thompson Sampling model selection
    thompson_model = None
    model_candidates = [
        {"provider": "ollama_cloud", "model": m}
        for m in ["deepseek-v4-flash", "kimi-k2", "glm-5.1", "qwen3-coder:480b",
                  "devstral-small-2:24b", "gemini-3-flash-preview"]
    ]
    if THOMPSON_ROUTING:
        thompson_result = thompson_sample_model(task_type, model_candidates)
        if thompson_result:
            thompson_model = thompson_result.get("model")
            pipeline_steps.append("thompson_sampling")

    # Step 6.5: Cold-start exploration — inject never-triggered resources
    COLD_START_RATE = 0.15
    if random.random() < COLD_START_RATE:
        try:
            cold_resources = _get_cold_resources(top_k=3)
            if cold_resources:
                for cr in cold_resources:
                    iso_candidates.insert(0, {
                        "name": cr["name"],
                        "iso_score": 0.3,
                        "resource_type": cr["resource_type"],
                        "_source": "cold_start",
                    })
                pipeline_steps.append("cold_start")
        except Exception as e:
            logger.debug(f"Cold-start failed: {e}")

    # Step 7: Fallback — keyword matching (specialty + tier)
    method = "v4_pipeline" if len(pipeline_steps) > 1 else "keyword_match"

    # Specialty check (always active as fallback)
    for spec_name, spec_config in SPECIALTY.items():
        for kw in spec_config["keywords"]:
            if kw.lower() in task.lower():
                skills = find_matching_skills(task, project, filtered_ids, preferred_names)
                # Merge intent results before returning (Phase C)
                if intent_results:
                    for ir in intent_results:
                        found = False
                        for s in skills:
                            if s["name"] == ir["name"]:
                                s["score"] = max(s["score"], ir["score"])
                                s["_source"] = "intent"
                                s["category"] = ir.get("category", "INVOKE")
                                found = True
                                break
                        if not found:
                            skills.insert(0, {
                                "name": ir["name"],
                                "score": ir["score"],
                                "category": ir.get("category", "INVOKE"),
                                "success_rate": 100,
                                "type": "skill",
                                "_source": "intent",
                            })
                    skills = sorted(
                        skills,
                        key=lambda s: (
                            0 if s.get("_source") == "intent" else 1,
                            -s.get("score", 0),
                            s.get("name", ""),
                        ),
                    )
                result = {
                    "task": task,
                    "project": project,
                    "specialty": spec_name,
                    "tier": 3 if spec_name in ("reasoning", "architecture") else 2,
                    "tier_name": "Specialty",
                    "model": thompson_model or spec_config["model"],
                    "token_limit": spec_config["token_limit"],
                    "skills": skills,
                    "method": method if thompson_model else "keyword_specialty",
                    "task_type": task_type,
                    "task_confidence": confidence,
                    "cluster": detected_cluster,
                    "pipeline": pipeline_steps,
                    "iso_top": iso_candidates[:5] if iso_candidates else [],
                    "thompson_model": thompson_model,
                    "latency_ms": round((time.time() - start_time) * 1000, 1),
                }
                # Enhance ranking (read-only re-rank); learning writes gated.
                try:
                    from learning import enhance_routing_result
                    result = enhance_routing_result(result, task, project)
                except Exception:
                    pass
                if write_feedback:
                    _log_decision(task, result, ide=ide)
                else:
                    result["write_feedback"] = False
                    result["method"] = result.get("method", "v4_pipeline") + "_pure"
                try:
                    from server.harness_engine.attach import attach_harness_to_result
                    execute = bool(
                        write_feedback
                        and HARNESS_RESEARCH_ENABLED
                        and RESEARCH_KEYWORDS.search(task)
                    )
                    result = attach_harness_to_result(
                        result, task, execute_research=execute,
                    )
                except Exception as e:
                    logger.debug("harness attach failed: %s", e)
                return result

    # Tier scoring (fallback model selection)
    scores = {}
    for tier_num, tier_config in TIERS.items():
        scores[tier_num] = score_tier(task, tier_config)

    best_tier = max(scores, key=scores.get)
    best_score = scores[best_tier]

    if best_score <= 0:
        best_tier = 2

    tier_config = TIERS[best_tier]
    skills = find_matching_skills(task, project, filtered_ids, preferred_names)

    # Phase C: Always do broad search too — cluster filtering can be too restrictive
    if filtered_ids:
        broad_skills = find_matching_skills(task, project, None, preferred_names)
        seen = {s['name'] for s in skills}
        for s in broad_skills:
            if s['name'] not in seen:
                skills.append(s)
                seen.add(s['name'])
            if len(skills) >= 20:
                break

    # Step 3.5 merge: Intent results get highest priority, then ISO, then keyword
    intent_seen = set()
    if intent_results:
        # Inject intent skills at the top with boosted scores
        for ir in intent_results:
            intent_seen.add(ir["name"])
            # Check if already in skills list — if so, boost its score
            found = False
            for s in skills:
                if s["name"] == ir["name"]:
                    s["score"] = max(s["score"], ir["score"])
                    s["_source"] = "intent"
                    s["category"] = ir.get("category", "INVOKE")
                    found = True
                    break
            if not found:
                skills.insert(0, {
                    "name": ir["name"],
                    "score": ir["score"],
                    "category": ir.get("category", "INVOKE"),
                    "success_rate": 100,
                    "type": "skill",
                    "_source": "intent",
                })

    # Phase C: ISO results as secondary priority
    if iso_candidates and use_embedding:
        iso_seen = set(intent_seen)
        for iso_c in iso_candidates:
            name = iso_c.get("name", "unknown")
            if name in iso_seen:
                continue
            iso_seen.add(name)
            found = False
            for s in skills:
                if s["name"] == name:
                    s["score"] = max(s["score"], iso_c.get("iso_score", 0))
                    s["_source"] = s.get("_source", "iso")
                    found = True
                    break
            if not found:
                skills.append({
                    "name": name,
                    "score": iso_c.get("iso_score", 0),
                    "category": "REVIEW",
                    "success_rate": 0,
                    "type": iso_c.get("resource_type", "skill"),
                    "_source": "iso",
                })

    # P3: do NOT merge harness/paper titles into skills (was polluting ranker).

    # Sort: intent first (by score), then ISO (by score), then keyword (by score)
    skills = sorted(
        skills,
        key=lambda s: (
            0 if s.get("_source") == "intent" else 1 if s.get("_source") == "iso" else 2,
            -s.get("score", 0),
            s.get("name", ""),
        ),
    )

    result = {
        "task": task,
        "project": project,
        "tier": best_tier,
        "tier_name": tier_config["name"],
        "model": thompson_model or tier_config["model"],
        "token_limit": tier_config["token_limit"],
        "skills": skills,
        "method": method,
        "scores": {str(k): v for k, v in scores.items()},
        "task_type": task_type,
        "task_confidence": confidence,
        "cluster": detected_cluster,
        "default_skill": default_skill,
        "default_agents": default_agents,
        "pipeline": pipeline_steps,
        "iso_top": iso_candidates[:5] if iso_candidates else [],
        "thompson_model": thompson_model,
        "latency_ms": round((time.time() - start_time) * 1000, 1),
    }

    # ── Layer 1-5: Enhance ranking (Thompson re-rank skills; no counters) ──
    try:
        from learning import enhance_routing_result
        result = enhance_routing_result(result, task, project)
    except Exception:
        pass

    if write_feedback:
        _log_decision(task, result, ide=ide)
    else:
        result["write_feedback"] = False
        if result.get("method") and not str(result["method"]).endswith("_pure"):
            result["method"] = f"{result['method']}_pure"

    # P3: attach harness plan + toolsets; execute research only when feedback path
    # and explicit research intent (artifacts only — never skills).
    try:
        from server.harness_engine.attach import attach_harness_to_result
        execute = bool(
            write_feedback
            and HARNESS_RESEARCH_ENABLED
            and RESEARCH_KEYWORDS.search(task)
        )
        result = attach_harness_to_result(
            result, task, execute_research=execute,
        )
    except Exception as e:
        logger.debug("harness attach failed: %s", e)

    return result


def _log_decision(task: str, result: dict, outcome: str = None,
                   ide: str = "unknown"):
    """Log routing decision to DB for analytics + optional learning.

    Always writes routing_log. Learning (counters/Thompson/edges) only runs
    for *terminal* outcomes (correct/wrong/partial/skip). Eval-dataset misses
    yield 'routed' which is telemetry-only after P0 record_outcome guard.
    
    DB lock safety: Each write operation opens its own short-lived connection,
    commits, and closes immediately. No nested or long-held connections.
    """
    if outcome is None:
        skills = [s["name"] for s in result.get("skills", [])]
        outcome = _evaluate_outcome(task, skills)
    
    # ── Step 1: Fast INSERT to routing_log (own connection, commit+close immediately) ──
    rowid = None
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        skills_json = json.dumps([s["name"] for s in result.get("skills", [])])[:500]
        cur = conn.cursor()
        # Instrumentation: these columns were schema-present but never written,
        # so routing latency / cost / context-injection / traffic class were
        # unmeasurable. Populated best-effort from `result`; absent values stay
        # NULL rather than being faked with 0.
        top_skill = result.get("skills", [{}])[0] if result.get("skills") else {}
        traffic = classify_traffic(task, result.get("project"), ide)
        # route_match: did the intent classifier drive the top pick?
        # 1 = intent-sourced, 0 = iso/fallback-sourced, NULL = undeterminable.
        src = top_skill.get("_source") if isinstance(top_skill, dict) else None
        route_match = 1 if src == "intent" else (0 if src else None)
        cur.execute("""
            INSERT INTO routing_log(task_summary, project, matched_tier, matched_model,
                                   matched_skills, outcome, skill_name, ide,
                                   action_type, intent, route_match, latency_ms,
                                   cost, tokens_used, injected_ctx_len, harness_tag)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (task[:200], result.get("project", "default"),
              result.get("tier", 0), result.get("model", "unknown"),
              skills_json, outcome,
              top_skill.get("name") if isinstance(top_skill, dict) else None,
              ide,
              result.get("method"),
              result.get("cluster") or result.get("task_type"),
              route_match,
              result.get("latency_ms"),
              result.get("cost"),
              result.get("tokens_used", 0),
              result.get("injected_ctx_len", 0),
              traffic))
        conn.commit()
        rowid = cur.lastrowid
        conn.close()
    except Exception as e:
        logger.warning(f"Routing log INSERT failed: {e}")

    # ── Hook resource-level learning only for terminal outcomes ──
    # record_outcome also guards non-terminal; calling it only when terminal
    # avoids extra DB work on the common 'routed' path.
    _TERMINAL = {"correct", "wrong", "partial", "skip"}
    if rowid and result.get("skills") and outcome in _TERMINAL:
        try:
            from learning import record_outcome
            skill_name = result["skills"][0]["name"]
            record_outcome(
                skill_name=skill_name,
                outcome=outcome,
                task_summary=task,
                project=result.get("project", "default"),
                ide=ide,
                routed_id=rowid
            )
        except Exception as e:
            logger.warning(f"Resource-level record_outcome failed: {e}")

    # ── Step 2: Thompson Sampling (separate connection, isolated write) ──
    if THOMPSON_ROUTING:
        try:
            from core.learner import NexusLearner
            learner = NexusLearner()
            learner.log_routing_decision(
                request_id=hashlib.md5(f"{task}{time.time()}".encode()).hexdigest()[:12],
                task_summary=task[:200],
                cluster=result.get("cluster"),
                task_type=result.get("task_type", "general"),
                provider="ollama_cloud",
                model=result.get("model", "unknown"),
                sampled_score=result.get("thompson_sampled_score", 0),
                benchmark_prior=0,
            )
            # ── Phase 7: implicit signal from routing itself ──
            learner.record_implicit_signal(
                provider="ollama_cloud",
                model=result.get("model", "unknown"),
                task_type=result.get("task_type", "general"),
                latency_ms=result.get("latency_ms", 0),
                tokens=0,
                success=True,
            )
            learner.close()
        except Exception as e:
            logger.warning(f"Thompson routing log failed: {e}")

    # ── Step 3: Cluster activation (own connection, fast write) ──
    if result.get("cluster") and CLUSTER_ROUTING:
        try:
            conn2 = sqlite3.connect(str(DB_PATH), timeout=30)
            conn2.execute("PRAGMA journal_mode=WAL")
            conn2.execute("PRAGMA busy_timeout=15000")
            conn2.execute("""
                INSERT INTO cluster_activation_log(query,project,activated_clusters,affinity_scores,total_resources_scanned,latency_ms)
                VALUES(?,?,?,?,?,?)
            """, (task[:200], "nexus",
                  json.dumps([result["cluster"]]),
                  json.dumps({"affinity": 0.0}),
                  100, result.get("latency_ms", 0.0)))
            conn2.commit()
            conn2.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: router.py '<task description>' [project_name]")
        print("       router.py --json '<task>' [project]")
        print("       router.py --cluster-on '<task>'   # enable cluster routing")
        print("       router.py --thompson-on '<task>'  # enable Thompson Sampling")
        sys.exit(1)

    json_mode = '--json' in sys.argv
    args = [a for a in sys.argv[1:] if a not in ('--json', '--cluster-on', '--thompson-on')]

    if '--cluster-on' in sys.argv:
        os.environ["NEXUS_CLUSTER_ROUTING"] = "true"
        CLUSTER_ROUTING = True
    if '--thompson-on' in sys.argv:
        os.environ["NEXUS_THOMPSON_ROUTING"] = "true"
        THOMPSON_ROUTING = True

    task = args[0]
    project = args[1] if len(args) > 1 else 'default'

    result = route_task(task, project)

    if json_mode:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Task:     {result['task']}")
        print(f"Type:     {result.get('task_type', '?')} (conf={result.get('task_confidence', 0):.2f})")
        print(f"Cluster:  {result.get('cluster', 'none')}")
        print(f"Tier:     {result.get('tier', '?')} ({result.get('tier_name', '?')})")
        print(f"Model:    {result['model']}")
        if result.get('thompson_model'):
            print(f"Thompson: {result['thompson_model']}")
        print(f"Tokens:   {result.get('token_limit') or 'unlimited'}")
        print(f"Pipeline: {' → '.join(result.get('pipeline', []))}")
        print(f"Latency:  {result.get('latency_ms', 0):.1f}ms")
        print(f"Skills:   {len(result['skills'])} matches")
        for s in result['skills']:
            print(f"  [{s['category']}] {s['name']} (score={s['score']}, sr={s['success_rate']}%)")
        if result.get('iso_top'):
            print(f"ISO Top:  {len(result['iso_top'])} candidates")
            for iso in result['iso_top'][:3]:
                print(f"  [ISO:{iso.get('iso_score',0):.3f}] {iso.get('name','?')} ({iso.get('resource_type','?')})")
