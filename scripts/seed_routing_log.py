#!/usr/bin/env python3
"""Run 50 diverse queries through route_v4 to seed the routing_log table
and build Thompson Sampling samples across all 8 domain clusters."""

import os, sys, json, time

sys.path.insert(0, os.path.expanduser("~/nexus"))
# Note: only add ~/nexus, NOT ~/nexus/core or ~/nexus/server individually.
# ~/nexus/core/server.py shadows the server/ package directory, breaking
# the `from server.router import ...` inside tool_attention_v4.py.

os.environ["TOKENIZERS_PARALLELISM"] = "false"

from server.tool_attention_v4 import route_v4

QUERIES = [
    # analyzer_planner (7)
    "fix bug in login handler",
    "debug memory leak in worker pool",
    "investigate slow query in user dashboard",
    "root cause of intermittent 503 errors",
    "analyze performance regression in build pipeline",
    "plan architecture for event-driven system",
    "trace data flow in payment processing",
    # architect (6)
    "design microservice decomposition for ecommerce",
    "architecture review of notification service",
    "spec out API gateway patterns",
    "design event schema for order processing",
    "evaluate CQRS vs event sourcing for audit log",
    "plan migration from monolith to modules",
    # frontend_ui (6)
    "create react dashboard component",
    "build responsive navigation bar",
    "implement dark mode toggle",
    "design form validation with error states",
    "add loading skeleton to data table",
    "optimize bundle size with code splitting",
    # backend_api (7)
    "implement REST endpoint for user profile",
    "design GraphQL schema for product catalog",
    "build authentication middleware",
    "add rate limiting to API gateway",
    "implement webhook delivery system",
    "design file upload with progress tracking",
    "build background job queue for email sending",
    # devops_infra (6)
    "deploy docker container to production",
    "set up CI/CD pipeline with GitHub Actions",
    "configure Kubernetes autoscaling",
    "write Terraform for AWS ECS cluster",
    "set up Prometheus monitoring stack",
    "implement blue-green deployment strategy",
    # quality_security (6)
    "write unit tests for API endpoint",
    "audit security vulnerabilities in dependencies",
    "implement OAuth2 flow with PKCE",
    "add SQL injection protection to queries",
    "run penetration test on login endpoint",
    "configure CSP headers for XSS protection",
    # knowledge_wiki (6)
    "document API reference for developer portal",
    "write onboarding guide for new engineers",
    "update README with architecture overview",
    "create CONTRIBUTING.md with coding standards",
    "document deployment runbook for on-call",
    "write ADR for database migration decision",
    # general (6)
    "what is the current system status",
    "show me recent changes to the codebase",
    "find documentation for the auth module",
    "list all available tools and resources",
    "help me understand the project structure",
    "generate a summary of recent deployments",
]

def run_batch():
    results = {"total": 0, "success": 0, "errors": 0, "fallbacks": 0, "by_cluster": {}}
    start = time.time()

    for q in QUERIES:
        results["total"] += 1
        try:
            raw = route_v4(q, auto_log=True)
            data = json.loads(raw)
            active = data.get("gating", {}).get("active_set", [])
            fallback = data.get("fallback_triggered", False)
            latency = data.get("latency_ms", {}).get("total", 0)
            top_name = active[0]["name"] if active else "none"

            if fallback:
                results["fallbacks"] += 1

            # Determine cluster from top resource name or fallback method
            fb_method = data.get("fallback_method", "none")
            cluster = "unknown"
            if top_name != "none":
                cluster = top_name.split("/")[0] if "/" in top_name else "general"
            elif fb_method != "none":
                cluster = f"fallback-{fb_method}"

            results["by_cluster"].setdefault(cluster, {"count": 0, "fallbacks": 0})
            results["by_cluster"][cluster]["count"] += 1
            if fallback:
                results["by_cluster"][cluster]["fallbacks"] += 1

            results["success"] += 1
            print(f"  ✓ {q[:45]:45s} → {top_name[:30]:30s}  "
                  f"{'FALLBACK' if fallback else '       '}  {latency:7.0f}ms")
        except Exception as e:
            results["errors"] += 1
            print(f"  ✗ {q[:45]:45s} → ERROR: {e}")

    elapsed = time.time() - start
    print()
    print("=" * 80)
    print(f"Batch complete: {results['success']}/{results['total']} success, "
          f"{results['fallbacks']} fallbacks, {elapsed:.0f}s total")
    print(f"By cluster:")
    for c, v in sorted(results["by_cluster"].items()):
        fb = f" ({v['fallbacks']} fallbacks)" if v["fallbacks"] else ""
        print(f"  {c:25s}: {v['count']}{fb}")
    print()

    # Summary routing_log state
    import sqlite3
    conn = sqlite3.connect(os.path.expanduser("~/nexus/db/nexus.db"), timeout=30)
    total_logs = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0]
    null_outcomes = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome IS NULL").fetchone()[0]
    print(f"Routing log now: {total_logs} total, {null_outcomes} pending feedback")
    conn.close()

    return results

if __name__ == "__main__":
    run_batch()
