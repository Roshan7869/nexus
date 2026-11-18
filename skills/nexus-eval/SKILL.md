---
name: nexus-eval
description: Evaluate NEXUS routing changes against the agreement gate before proposing them.
---

# nexus-eval

1. Run the 20-query A/B suite (`router_ab_suite`) for baseline vs candidate.
2. Require cross-router agreement gain with no p95 regression (`route_ab_compare`).
3. Record every judged task via `report_outcome` (feeds Thompson sampling).

Rules: no default-weight change without a passing A/B receipt pasted in the PR.
