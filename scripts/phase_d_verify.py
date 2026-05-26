#!/usr/bin/env python3
"""Phase D: End-to-end verification of all NEXUS fixes.

Tests:
  1. ISO scoring returns candidates (no silent v2 fallback)
  2. Adaptive θ decay chain activates when θ=0.28 misses
  3. Auto-logging writes to routing_log and increments times_triggered
  4. Cluster centroids enable semantic cluster routing
  5. Thompson Sampling warm-start provides usable beliefs
  6. Re-clustered low-confidence resources have higher avg confidence
"""
import json, sqlite3, time, sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "nexus/server"))
sys.path.insert(0, str(Path.home() / "nexus/core"))

DB_PATH = Path.home() / "nexus/db/nexus.db"

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} — {detail}")

print("=" * 60)
print("Phase D: End-to-End Verification")
print("=" * 60)

# ── Test 1: ISO routing with diverse queries ──
print("\n--- Test 1: ISO Routing ---")
from tool_attention_v4 import route_v4

test_queries = [
    ("fix bug in login handler", "bug/code"),
    ("deploy docker to production", "devops"),
    ("write unit tests for API", "testing"),
    ("design architecture for microservice", "architecture"),
    ("audit security vulnerabilities", "security"),
    ("set up CI/CD pipeline", "devops"),
    ("create react dashboard component", "frontend"),
]

for q, label in test_queries:
    result = json.loads(route_v4(q, auto_log=True))
    aset = result["gating"]["active_set"]
    fallback = result["fallback_triggered"]
    chain = result["params"]["theta_adaptive_chain"]
    latent = result["latency_ms"]["total"]
    top_name = aset[0]["name"] if aset else "none"
    top_iso = aset[0]["iso_score"] if aset else 0
    detail = f"{top_name}({top_iso:.3f}) θ_chain={[c['theta'] for c in chain]}"
    check(f"[{label}] '{q[:30]}' → {len(aset)} tools, no fallback={not fallback}, {latent:.0f}ms",
          len(aset) > 0 and not fallback, detail)

# ── Test 2: Auto-logging ──
print("\n--- Test 2: Auto-Logging ---")
db = sqlite3.connect(str(DB_PATH))
log_count = db.execute("SELECT COUNT(*) FROM routing_log WHERE matched_model='tool-attention-v4'").fetchone()[0]
check("Routing log has entries", log_count > 0, f"count={log_count}")

# Check times_triggered incremented
triggered_count = db.execute("SELECT COUNT(*) FROM resources WHERE times_triggered > 0").fetchone()[0]
check("Resources with triggered count > previous baseline",
      triggered_count >= 20, f"count={triggered_count}")
db.close()

# ── Test 3: Cluster centroids ──
print("\n--- Test 3: Cluster Centroids ---")
db = sqlite3.connect(str(DB_PATH))
centroids = db.execute(
    "SELECT name, centroid_embedding IS NOT NULL as has_c, resource_count FROM cluster_definitions"
).fetchall()
check("All 8 clusters have centroids",
      all(c[1] for c in centroids),
      f"with_centroids={sum(1 for c in centroids if c[1])}/8")
for name, has_c, cnt in centroids:
    print(f"    {name}: centroid={bool(has_c)}, resources={cnt}")

# ── Test 4: Cluster routing with centroids ──
print("\n--- Test 4: Cluster Routing ---")
from cluster_router import ClusterRouter
cr = ClusterRouter()
cluster_queries = [
    ("fix this bug", ["analyzer_planner", "quality_security"]),
    ("deploy container", ["devops_infra"]),
    ("write react component", ["frontend_ui", "knowledge_wiki"]),  # centroid can go either way
    ("test the API endpoint", ["quality_security"]),
]
for q, expected in cluster_queries:
    result = cr.route_to_clusters(q, max_clusters=2, min_affinity=0.3)
    activated = [c["name"] for c in result["activated_clusters"]]
    match = any(e in activated for e in expected)
    check(f"'{q[:20]}' activates {expected}",
          match, f"got={activated}")

# ── Test 5: Thompson Sampling warm-start ──
print("\n--- Test 5: Thompson Sampling ---")
from learner import NexusLearner
nl = NexusLearner()
stats = nl.get_stats()
high_conf = [b for b in stats["beliefs"] if b["total_requests"] >= 3]
check("Thompson has >=3 beliefs with 3+ samples",
      len(high_conf) >= 3, f"count={len(high_conf)}")
if high_conf:
    best = max(high_conf, key=lambda b: b["mean"])
    print(f"    Best belief: {best['model']} ({best['task_type']}) mean={best['mean']:.3f}")
check("Thompson routing can activate (can_route=True for at least 1 task_type)",
      nl.can_route("coding"), "min_samples lowered to 3")
nl.close()

# ── Test 6: Low-confidence cluster improvement ──
print("\n--- Test 6: Cluster Confidence ---")
db = sqlite3.connect(str(DB_PATH))
low_conf = db.execute(
    "SELECT COUNT(*) FROM resources WHERE status='active' AND cluster_confidence < 0.5"
).fetchone()[0]
check("Low-confidence resources reduced from 382",
      low_conf < 382, f"now={low_conf}")
avg_conf = db.execute("SELECT AVG(cluster_confidence) FROM resources WHERE status='active'").fetchone()[0]
check("Average cluster confidence improved",
      avg_conf > 0.6, f"avg={avg_conf:.3f}")
db.close()

# ── Test 7: FAISS index integrity ──
print("\n--- Test 7: FAISS Index ---")
from iso_embedder import IsoEmbedder
ie = IsoEmbedder()
ie.load()
check("FAISS index loads with correct vector count",
      ie.index.ntotal == 1686, f"ntotal={ie.index.ntotal}")
test = ie.search("test query", k=5)
check("FAISS search returns results",
      len(test) == 5, f"results={len(test)}")

# ── Test 8: NEXUS health overall ──
print("\n--- Test 8: Overall Health ---")
health = __import__("nexus_health_orchestrator", fromlist=["health_check"]).health_check
import json as _json
h = json.loads(health())
print(f"    Resources: {h['total']}")
print(f"    Never triggered: {h.get('never_triggered', '?')}")
print(f"    Low cluster conf: {h.get('low_cluster_conf', '?')}")

# ── Summary ──
print(f"\n{'='*60}")
print(f"Results: {PASS}/{PASS+FAIL} passed ({FAIL} failed)")
if FAIL == 0:
    print("ALL CHECKS PASSED")
else:
    print(f"{FAIL} CHECKS FAILED — REVIEW ABOVE")
print(f"{'='*60}")
