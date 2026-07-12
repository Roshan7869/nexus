#!/usr/bin/env python3
"""Benchmark: v4 route_task vs v5 unified_route_v5 on 10 standard queries."""

import sys, os, time

sys.path.insert(0, '/home/roshan/nexus')
sys.path.insert(0, '/home/roshan/nexus/server')
os.environ['PYTHONPATH'] = '/home/roshan/nexus'

from router import route_task

os.environ['USE_UNIFIED_ROUTER'] = '2'
from router_v2.v5 import unified_route_v5

QUERIES = [
    "fix a login registration bug",
    "design a landing page for a SaaS product",
    "write PostgreSQL query for user analytics",
    "build a React component for data table",
    "deploy Docker container to production",
    "test API endpoint for authentication",
    "write documentation for REST API",
    "implement OAuth2 flow",
    "configure CI/CD pipeline with GitHub Actions",
    "migrate database schema for users table",
]

def extract_skill_names_v4(result):
    skills = result.get("skills", []) if isinstance(result, dict) else []
    return [s.get("name", "") for s in skills if isinstance(s, dict)]

def extract_skill_names_v5(result):
    merged = result.get("merged", []) if isinstance(result, dict) else []
    return [m.get("name", "") for m in merged if isinstance(m, dict)]

def overlap_at_5(v4_names, v5_names):
    return len(set(v4_names[:5]) & set(v5_names[:5]))

results = []
v4_latencies = []
v5_latencies = []

for i, q in enumerate(QUERIES):
    print(f"\n--- Query {i+1}/10: {q!r} ---")

    t0 = time.time()
    v4_res = route_task(q, project="default", use_embedding=True)
    v4_t = (time.time() - t0) * 1000
    v4_names = extract_skill_names_v4(v4_res)
    v4_top5 = v4_names[:5]
    v4_latencies.append(v4_t)

    t0 = time.time()
    v5_res = unified_route_v5(q, top_k=5, cold_fraction=0.4)
    v5_t = (time.time() - t0) * 1000
    v5_names = extract_skill_names_v5(v5_res)
    v5_top5 = v5_names[:5]
    v5_latencies.append(v5_t)

    ov = overlap_at_5(v4_names, v5_names)

    results.append((q, v4_top5, v5_top5, ov, v4_t, v5_t))
    print(f"  v4: {v4_t:8.1f}ms  v5: {v5_t:8.1f}ms  overlap@5: {ov}")

print()
print("## Results")
print()
print("| # | Query | v4 Skills (top 5) | v5 Skills (top 5) | Overlap@5 | v4 Latency (ms) | v5 Latency (ms) |")
print("|---|-------|-------------------|-------------------|-----------|-----------------|-----------------|")
for idx, (q, v4_top5, v5_top5, ov, v4_t, v5_t) in enumerate(results, 1):
    q_short = q[:50]
    v4_str = ", ".join(v4_top5) if v4_top5 else "(none)"
    v5_str = ", ".join(v5_top5) if v5_top5 else "(none)"
    print(f"| {idx} | {q_short} | {v4_str} | {v5_str} | {ov} | {v4_t:.1f} | {v5_t:.1f} |")

avg_overlap = sum(r[3] for r in results) / len(results)
avg_v4 = sum(v4_latencies) / len(v4_latencies)
avg_v5 = sum(v5_latencies) / len(v5_latencies)
ratio = avg_v5 / avg_v4 if avg_v4 > 0 else float('inf')

print()
print("## Aggregate Summary")
print()
print(f"| Metric | Value |")
print(f"|--------|-------|")
print(f"| Average Overlap@5 | {avg_overlap:.2f} |")
print(f"| Average v4 Latency | {avg_v4:.1f} ms |")
print(f"| Average v5 Latency | {avg_v5:.1f} ms |")
print(f"| Latency Ratio (v5/v4) | {ratio:.2f}x |")
print(f"| Total Queries | {len(results)} |")
