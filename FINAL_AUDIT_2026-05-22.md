# NEXUS Final Audit Report — 2026-05-22

## Health Score: 85/100 (+130% from baseline 37/100)

### Score Breakdown

| Category | Score | Max | Detail |
|----------|-------|-----|--------|
| Resource Coverage | 7.8 | 12 | 51.7% triggered (target 80%), 693 cold resources |
| Thompson Sampling | 11.8 | 15 | 63.1% warm beliefs (1804/2858), 533 neighbor-warmstarted |
| Graph Quality | 15.0 | 15 | 6864 edges, 60.1% learned weights, 4 edge types |
| Cluster System | 12.0 | 12 | 8 clusters, all centroids, cluster_membership authoritative |
| Learning Pipeline | 10.2 | 15 | record_outcome wired, 4128 edges updated, reflection active |
| Ingestion Pipeline | 7.0 | 10 | Wired to all tables, 0 chunks ingested yet |
| Router Correctness | 10.0 | 10 | Uses cluster_membership, epsilon-greedy, auto-close |
| Self-Healing | 11.0 | 11 | SQL triggers, reflection, degradation, edge multiplicative |

### Fixes Applied This Session

1. **P0-2: Cluster source of truth** — Router now reads from `cluster_membership` (L702-720) instead of `resources.cluster`. Fallback to column if JOIN fails.
2. **P0-3: Belief backfill** — Ran backfill_beliefs.py. 457 resources with routing_log outcomes now have warm beliefs. 533 cold resources warm-started from neighbor edges.
3. **P0-4: Neighbor warm-start** — New beliefs seeded with α=1+(neighbor_α-1)*0.3, β=1+(neighbor_β-1)*0.3. Coverage went from 54.3% to 63.1%.
4. **P1-1: Ingestion pipeline wired** — nexus-ingest.py now writes to `cluster_membership`, `model_beliefs`, `ingestion_sources`, and `ingestion_chunks` on every ingestion.
5. **P2-1: Reflection verified** — Reflection fires on "wrong" outcomes. Partial outcomes reflect when error_rate > 30%. 10 failures, 20 fixes recorded.
6. **P3-1: Edge learning verified** — 4128/6864 edges (60.1%) have learned weights from multiplicative updates (correct: *1.05 cap 1.0, wrong: *0.85 floor 0.05).
7. **Cluster divergence resolved** — 42 multi-cluster resources have `resources.cluster` = primary cluster. `cluster_membership` lists all clusters. 6 SQL triggers keep them synced.

### Remaining Gaps (15 points to 100)

| Gap | Points | Fix |
|-----|--------|-----|
| Cold resources (48.3%) | -4.2 | Traffic growth + description-based embedding similarity for cold-start |
| Belief coverage (63.1%) | -3.2 | More traffic + routing_log backfill |
| Ingestion untested (0 chunks) | -3.0 | Run `nexus-ingest.py --source /path` and verify |
| Learning pipeline feedback depth | -4.8 | Partial outcome reflection only for error>30%; increase with more traffic |

### How to Reach 95+

1. **Exercise ingestion** — Run `python3 scripts/nexus-ingest.py --source /home/roshan/nexus/server --tag nexus-code` to ingest project code and create real chunks/beliefs
2. **Increase traffic** — Use the MCP tools daily; each call populates routing_log, updates beliefs, and learns edges
3. **Lower reflection threshold** — Change `error_rate > 0.30` to `error_rate > 0.15` in learning.py L216 once traffic grows
4. **Add description-based cold-start** — For 693 cold resources, generate embeddings from their body_text/description and match to nearest warm resource for belief seeding

### Key Architecture Decisions

- **cluster_membership is authoritative** — `resources.cluster` is a denormalized fallback, synced by SQL triggers
- **Multiplicative edge updates** — correct: *1.05 (cap 1.0), wrong: *0.85 (floor 0.05). Preserves relative ordering, avoids inflation.
- **Epsilon-greedy with decay** — ε = 0.15·e^(-0.0005·N), currently at floor (ε≈0.02) with N=4591
- **Thompson Sampling** — Per-resource-per-task α/β beliefs. Warm-started from neighbors for cold resources.
- **Reflection** — Fires on wrong outcomes + partials with error>30%. Logs failures, proposes alternatives.
- **Auto-close** — Stale "routed" entries auto-closed as "partial" after configurable timeout.