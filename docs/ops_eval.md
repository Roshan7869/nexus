# NEXUS P5 Ops Eval

## Commands

```bash
# Metrics + gates (fast)
python scripts/run_ops_eval.py

# + quick A/B (5 queries, ~1–2 min with embeddings)
python scripts/run_ops_eval.py --ab

# + full 20-query A/B ship gate
python scripts/run_ops_eval.py --full-ab --fail-on-gate

# CI
bash scripts/ci_eval.sh
bash scripts/ci_eval.sh --ab
```

## HTTP

| Endpoint | Purpose |
|----------|---------|
| `GET /v1/ops` | Full ops report + gates |
| `GET /v1/ops/coverage` | Terminal close rate |
| `GET /v1/ops/harness` | FTSR / MRC / TLT / CDR |
| `GET /v1/health` | Router mode + contract |

Dashboard (port 8765): `/api/metrics`, `/api/p5`

## Gates (hard)

| Gate | Target |
|------|--------|
| terminal_close_rate | ≥ 80% |
| router_mode_match | USE_UNIFIED_ROUTER matches live |
| zero_token_active | 0 |
| cluster_divergence | 0 |
| db_ok | true |

Never-triggered % is reported but not a hard ship gate (campaign metric).
A/B top-1 agreement target 0.95 is optional (`--full-ab`).
