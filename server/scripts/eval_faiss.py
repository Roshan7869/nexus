#!/usr/bin/env python3
"""Evaluate FAISS routing accuracy against labeled dataset.

Usage:
    python3 eval_faiss.py                          # Run full eval
    python3 eval_faiss --top-k 5                   # Use top-5 instead of top-10
    python3 eval_faiss --sample 20                 # Run on 20 random queries
"""
import json, sys, time, random
from pathlib import Path

sys.path.insert(0, str(Path.home() / 'nexus'))
from server.router import route_task


def evaluate(dataset_path: str, top_k: int = 10, sample: int = None) -> dict:
    with open(dataset_path) as f:
        dataset = json.load(f)

    if sample and sample < len(dataset):
        dataset = random.sample(dataset, sample)

    results = []
    precision_at_5 = []
    precision_at_10 = []
    recall_at_5 = []
    recall_at_10 = []

    for entry in dataset:
        task = entry["task_text"]
        expected = set(entry.get("expected_resource_names", []))

        if not expected:
            # Adversarial/no-op queries — should return empty or low-confidence
            result = route_task(task, use_embedding=False)
            skills = [s["name"] for s in result.get("skills", [])]
            is_empty = len(skills) == 0 or all(s["score"] < 0.3 for s in result.get("skills", []))
            results.append({
                "query_id": entry["query_id"],
                "task": task[:80],
                "expected": [],
                "returned": skills[:5],
                "type": "adversarial",
                "pass": is_empty,
            })
            continue

        result = route_task(task, use_embedding=True)
        returned = [s["name"] for s in result.get("skills", [])]
        returned_set = set(returned)

        # Precision@k: of top-k returned, how many are in expected
        top5 = returned[:5]
        top10 = returned[:10]
        p5 = len(set(top5) & expected) / max(len(top5), 1)
        p10 = len(set(top10) & expected) / max(len(top10), 1)

        # Recall@k: of expected, how many are in top-k returned
        r5 = len(set(top5) & expected) / max(len(expected), 1)
        r10 = len(set(top10) & expected) / max(len(expected), 1)

        precision_at_5.append(p5)
        precision_at_10.append(p10)
        recall_at_5.append(r5)
        recall_at_10.append(r10)

        results.append({
            "query_id": entry["query_id"],
            "task": task[:80],
            "expected": list(expected),
            "returned": returned[:10],
            "precision@5": round(p5, 3),
            "precision@10": round(p10, 3),
            "recall@5": round(r5, 3),
            "recall@10": round(r10, 3),
        })

    # Adversarial pass rate
    adversarial = [r for r in results if r.get("type") == "adversarial"]
    adv_pass = sum(1 for r in adversarial if r["pass"]) / max(len(adversarial), 1)

    non_adv = [r for r in results if r.get("type") != "adversarial"]
    avg_p5 = sum(r["precision@5"] for r in non_adv) / max(len(non_adv), 1)
    avg_p10 = sum(r["precision@10"] for r in non_adv) / max(len(non_adv), 1)
    avg_r5 = sum(r["recall@5"] for r in non_adv) / max(len(non_adv), 1)
    avg_r10 = sum(r["recall@10"] for r in non_adv) / max(len(non_adv), 1)

    report = {
        "dataset": dataset_path,
        "total_queries": len(dataset),
        "non_adversarial": len(non_adv),
        "adversarial": len(adversarial),
        "precision@5": round(avg_p5, 3),
        "precision@10": round(avg_p10, 3),
        "recall@5": round(avg_r5, 3),
        "recall@10": round(avg_r10, 3),
        "adversarial_pass_rate": round(adv_pass, 3),
        "failing_queries": [
            {"query_id": r["query_id"], "task": r["task"], "expected": r["expected"], "returned": r["returned"]}
            for r in non_adv if r["precision@5"] == 0
        ][:10],
    }

    return report


if __name__ == "__main__":
    top_k = 10
    sample = None
    if "--top-k" in sys.argv:
        idx = sys.argv.index("--top-k")
        top_k = int(sys.argv[idx + 1])
    if "--sample" in sys.argv:
        idx = sys.argv.index("--sample")
        sample = int(sys.argv[idx + 1])

    dataset = str(Path.home() / "nexus/server/scripts/faiss_eval_dataset.json")
    print(f"Evaluating FAISS routing on {dataset}")
    if sample:
        print(f"  Sample: {sample} queries")
    print(f"  top-k: {top_k}")
    print()

    start = time.time()
    report = evaluate(dataset, top_k=top_k, sample=sample)
    elapsed = time.time() - start

    print(f"Results ({elapsed:.1f}s):")
    print(f"  Total queries:    {report['total_queries']}")
    print(f"  Non-adversarial:  {report['non_adversarial']}")
    print(f"  Adversarial:      {report['adversarial']}")
    print(f"  Precision@5:      {report['precision@5']}")
    print(f"  Precision@10:     {report['precision@10']}")
    print(f"  Recall@5:         {report['recall@5']}")
    print(f"  Recall@10:        {report['recall@10']}")
    print(f"  Adv pass rate:    {report['adversarial_pass_rate']}")

    if report.get("failing_queries"):
        print(f"\nTop {len(report['failing_queries'])} failing queries:")
        for fq in report["failing_queries"][:5]:
            print(f"  [{fq['query_id']}] {fq['task']}")
            print(f"    Expected: {fq['expected']}")
            print(f"    Returned: {fq['returned']}")

    # Save report
    report_path = str(Path.home() / "nexus/server/scripts/faiss_eval_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved to {report_path}")
