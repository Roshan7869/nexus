"""Advisory full-catalog sampling; probabilities describe selection, not success."""
from __future__ import annotations

import hashlib
import math
import random
import re
import secrets
import time
import uuid

from server.capability_catalog import connect_db
from server.capability_events import _append, _json, _session
from server import capability_policy
from server.cluster_config import CLUSTER_DEFINITIONS

NICHE_VERSION = "cluster-keywords-v1"
POLICY_VERSION = "lexical-beta-v1:epsilon=.05:temperature=.20"


def classify_niche(query, project):
    """Reuse the existing domain labels without importing DB/model classifiers."""
    text = (query + " " + str(project)).lower()
    scores = {name: sum(bool(re.search(r"\b" + re.escape(word.lower()) + r"\b", text))
                        for word in config.get("trigger_keywords", []))
              for name, config in CLUSTER_DEFINITIONS.items()}
    label = max(sorted(scores), key=scores.get) if any(scores.values()) else "general"
    return {"niche": label, "niche_version": NICHE_VERSION}


def score_distribution(candidates, beliefs, epsilon=0.05, temperature=0.20):
    """Return all candidate probabilities from relevance and hierarchical counts.

    beliefs maps (capability_id, version) to global_success/global_failure and
    niche_success/niche_failure. Legacy counters are deliberately ignored.
    """
    if not 0 < epsilon <= 1 or not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("INVALID_SAMPLING_POLICY")
    if not candidates:
        return []
    scored = []
    for item in candidates:
        counts = beliefs.get((item["capability_id"], item["version"]), {})
        gs, gf, ns, nf = (counts.get(key, 0) for key in (
            "global_success", "global_failure", "niche_success", "niche_failure"))
        if any(type(n) is not int or n < 0 for n in (gs, gf, ns, nf)):
            raise ValueError("INVALID_BELIEF_COUNTS")
        global_mean = (1 + gs) / (2 + gs + gf)
        predicted = (2 * global_mean + ns) / (2 + ns + nf)
        relevance = float(item.get("relevance", 0))
        if not math.isfinite(relevance):
            relevance = 0.0
        relevance = min(1.0, max(0.0, relevance))
        scored.append({"capability_id": item["capability_id"], "version": item["version"],
                       "relevance": relevance, "predicted_success": predicted,
                       "utility": .65 * relevance + .35 * predicted})
    maximum = max(item["utility"] for item in scored)
    weights = [math.exp((item["utility"] - maximum) / temperature) for item in scored]
    total = math.fsum(weights)
    for item, weight in zip(scored, weights):
        item["probability"] = (1 - epsilon) * weight / total + epsilon / len(scored)
    return scored


def _normalize_distribution(scored_subset, epsilon=0.05, temperature=0.20):
    """Normalize probabilities across a remaining subset using precomputed utilities."""
    if not scored_subset:
        return []
    maximum = max(item["utility"] for item in scored_subset)
    weights = [math.exp((item["utility"] - maximum) / temperature) for item in scored_subset]
    total = math.fsum(weights)
    n = len(scored_subset)
    result = []
    for item, weight in zip(scored_subset, weights):
        prob = (1 - epsilon) * weight / total + epsilon / n
        result.append({
            "capability_id": item["capability_id"],
            "version": item["version"],
            "relevance": item["relevance"],
            "predicted_success": item["predicted_success"],
            "utility": item["utility"],
            "probability": prob,
        })
    return result


class CapabilityRecommender:
    def __init__(self, db_path, catalog, policy=None):
        if db_path is None:
            raise ValueError("db_path is required")
        self.db_path, self.catalog = db_path, catalog
        self.policy = capability_policy if policy is None else policy

    def _eligible(self, principal, context, descriptor):
        visible = self.policy.visibility(principal, descriptor)
        if not (visible.get("visible", visible.get("allowed", False))
                if isinstance(visible, dict) else visible):
            return False, "hidden"
        rules = descriptor.get("execution_policy", {})
        if rules.get("principals") and not any(p in rules["principals"] for p in ("*", principal["id"])):
            return False, "EXECUTION_DENIED"
        result = self.policy.eligibility(context, descriptor, context.get("provider_snapshot", {}))
        if isinstance(result, dict):
            return bool(result.get("eligible", result.get("allowed", False))), result.get("reason")
        return bool(result), None

    def _lexical_relevance(self, query, project, candidates):
        """Bound descriptor text and retrieval time; unscored IDs keep exploration mass."""
        tokens = set(re.findall(r"\w+", query.lower())[:256])
        deadline, values = time.monotonic() + 2.0, {}
        degraded = []
        for item in candidates:
            if time.monotonic() >= deadline:
                degraded.append("lexical_deadline")
                break
            descriptor = item["descriptor"]
            words = descriptor.get("_words")
            if words is None:
                text = f"{descriptor.get('name', '')} {descriptor.get('logical_key', '')} {str(descriptor.get('description', ''))[:1024]} {descriptor.get('tags', '')} {descriptor.get('capability_id', '')}"
                words = set(re.findall(r"\w+", text.lower()))
                descriptor["_words"] = words
            overlap = len(tokens & words) / max(1, len(tokens))
            affinities = descriptor.get("project_relevance", {})
            boost = affinities.get(project, 0) if isinstance(affinities, dict) else 0
            try:
                boost = float(boost)
                if not math.isfinite(boost):
                    raise ValueError("nonfinite")
            except (ValueError, TypeError, OverflowError):
                boost = 0
                if "invalid_project_relevance" not in degraded:
                    degraded.append("invalid_project_relevance")
            values[item["capability_id"]] = min(1.0, max(0.0, .8 * overlap + .2 * boost))
        return values, degraded

    def recommend(self, session_id, query, k=8, seed=None, principal=None):
        if type(k) is not int or not 1 <= k <= 50:
            raise ValueError("INVALID_K")
        if not isinstance(query, str) or len(query.encode("utf-8")) > 16384:
            raise ValueError("INVALID_QUERY")
        if seed is not None and type(seed) is not int:
            raise ValueError("INVALID_SEED")
        with connect_db(self.db_path) as conn:
            session = _session(conn, session_id, principal)
            providers = {row["provider_id"]: dict(row) for row in conn.execute(
                "SELECT provider_id,generation,state FROM provider_generations "
                "WHERE state='active' ORDER BY generation")}
        principal = principal or {"id": session["principal_id"], "harness": session["harness_id"],
                                  "projects": [session["project"]]}
        context = {**session["context"], "project": session["project"],
                   "harness": session["harness_id"], "harness_id": session["harness_id"],
                   "principal": principal, "provider_snapshot": providers}
        niche = classify_niche(query, context["project"])
        context.setdefault("niche", niche["niche"])
        context.setdefault("niche_version", niche["niche_version"])
        candidates, excluded, cursor, revision = [], [], None, None
        seen_cursors, seen_ids = set(), set()
        while True:
            page = self.catalog.list_capabilities(principal, cursor=cursor, limit=1000, query=None)
            page_revision = page.get("catalog_revision", page.get("revision"))
            if revision is None:
                revision = page_revision
            if page_revision != revision:
                raise ValueError("CATALOG_REVISION_CHANGED")
            for descriptor in page.get("items", page.get("capabilities", [])):
                capability_id = descriptor.get("capability_id", descriptor.get("canonical_id"))
                version = descriptor.get("version", descriptor.get("current_version"))
                if not capability_id or version is None or capability_id in seen_ids:
                    raise ValueError("INVALID_CATALOG_PAGE")
                seen_ids.add(capability_id)
                allowed, reason = self._eligible(principal, context, descriptor)
                if allowed:
                    candidates.append({"capability_id": capability_id, "version": version,
                                       "descriptor": descriptor})
                elif reason != "hidden":
                    excluded.append({"capability_id": capability_id, "reason": reason})
            cursor = page.get("next_cursor")
            if not cursor:
                break
            if cursor in seen_cursors:
                raise ValueError("CATALOG_CURSOR_LOOP")
            seen_cursors.add(cursor)
        candidates.sort(key=lambda item: item["capability_id"])
        if not revision and not candidates:
            return {"decision_id": None, "session_id": session_id, "recommendations": [],
                    "distribution": [], "draws": [], "catalog_revision": revision or 0,
                    "policy_version": POLICY_VERSION, "niche": context["niche"],
                    "niche_version": context["niche_version"], "seed": seed,
                    "discovery": {"method": "list_capabilities", "cursor": None},
                    "degradation": ["catalog_empty"], "excluded": excluded}
        relevance, degradation = self._lexical_relevance(query, context["project"], candidates)
        invalid = any(not isinstance(value, (int, float)) or not math.isfinite(value)
                      for value in relevance.values())
        if invalid:
            relevance, fallback = CapabilityRecommender._lexical_relevance(
                self, query, context["project"], candidates)
            degradation += ["invalid_relevance_lexical_fallback"] + fallback
        for item in candidates:
            item["relevance"] = relevance.get(item["capability_id"], 0)
        seed = secrets.randbits(63) if seed is None else seed
        rng, draws, recommendations = random.Random(seed), [], []
        decision_id = uuid.uuid4().hex
        with connect_db(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            _session(conn, session_id, principal)
            if revision is None:
                row = conn.execute("SELECT MAX(revision) AS revision FROM catalog_revisions").fetchone()
                revision = row["revision"]
            beliefs = {}
            for row in conn.execute("SELECT * FROM capability_beliefs"):
                key = (row["capability_id"], row["version"])
                counts = beliefs.setdefault(key, {"global_success": 0, "global_failure": 0,
                                                  "niche_success": 0, "niche_failure": 0})
                counts["global_success"] += row["successes"]
                counts["global_failure"] += row["failures"]
                if row["niche"] == context["niche"] and row["niche_version"] == str(context["niche_version"]):
                    counts["niche_success"], counts["niche_failure"] = row["successes"], row["failures"]
            distribution = score_distribution(candidates, beliefs)
            remaining = list(distribution)
            for index in range(min(k, len(remaining))):
                conditional = distribution if index == 0 else _normalize_distribution(remaining)
                uniform, cumulative = rng.random(), 0.0
                selected = conditional[-1]
                for item in conditional:
                    cumulative += item["probability"]
                    if uniform < cumulative:
                        selected = item
                        break
                recommendations.append({**selected, "selection_probability": selected["probability"]})
                draws.append({"index": index, "seed": seed, "uniform": uniform,
                              "remaining": [item["capability_id"] for item in remaining],
                              "distribution": conditional, "capability_id": selected["capability_id"],
                              "conditional_probability": selected["probability"]})
                remaining = [item for item in remaining if item["capability_id"] != selected["capability_id"]]
            snapshot = [{"capability_id": item["capability_id"], "version": item["version"]}
                        for item in candidates]
            candidate_hash = hashlib.sha256(_json(snapshot).encode()).hexdigest()
            policy_version = POLICY_VERSION + ":" + str(getattr(
                self.policy, "POLICY_VERSION", getattr(self.policy, "version", "custom")))
            logged = {"seed": seed, "context": context, "query": query, "candidates": snapshot,
                      "distribution": distribution, "draws": draws, "degradation": degradation,
                      "policy": {"epsilon": .05, "temperature": .20, "mode": "bounded_lexical"}}
            conn.execute("INSERT INTO recommendation_decisions "
                "(decision_id,session_id,catalog_revision,policy_version,niche_version,candidate_hash,draws_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)", (decision_id, session_id, revision, policy_version,
                str(context["niche_version"]), candidate_hash, _json(logged), time.time()))
            for item in recommendations:
                _append(conn, {"session_id": session_id, "type": "recommended",
                               "payload": {"decision_id": decision_id, **item}})
        return {"decision_id": decision_id, "session_id": session_id, "recommendations": recommendations,
                "distribution": distribution, "draws": draws, "catalog_revision": revision,
                "policy_version": policy_version, "niche": context["niche"],
                "niche_version": context["niche_version"], "seed": seed,
                "discovery": {"method": "list_capabilities", "cursor": None},
                "degradation": degradation, "excluded": excluded}
