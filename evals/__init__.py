"""NEXUS eval harness — golden datasets, offline scorer, replay, sampled judge.

Layout (Week1 cards 3.x / 5.x):
    golden/routing_cases.yaml   versioned golden dataset (never tune on holdout)
    golden/holdout.yaml         20% held out — REPORTED only
    scorer.py                   pure scoring functions, no I/O, no server imports
    run_eval.py                 offline runner: route_for_task(open_session=False)
    replay.py                   A/B router versions over recorded routing_log
    judge.py                    sampled LLM-judge (network, Day 5)
    results/                    archived run outputs (committed)
"""
