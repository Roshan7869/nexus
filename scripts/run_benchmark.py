#!/usr/bin/env python3
"""
Nexus Benchmark Runner
Run official benchmarks (MMLU, HumanEval, MATH500) against configured models.
Usage: python scripts/run_benchmark.py --benchmark mmlu
"""
import argparse, asyncio, json, logging, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import NexusConfig
from core.metrics import MetricsStore
from core.router import BenchmarkStore, BenchmarkRunner
from providers.registry import build_provider_map

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("nexus.benchmark")


async def run_benchmarks(config_path: str, benchmark_names: list):
    config = NexusConfig.from_yaml(config_path)
    metrics_store = MetricsStore()
    benchmark_store = BenchmarkStore()
    providers = build_provider_map(config)

    runner = BenchmarkRunner(benchmark_store, providers)
    results = await runner.run_all_benchmarks()

    if results:
        logger.info(f"Benchmark results: {json.dumps(results, indent=2)}")
    else:
        logger.info("No results yet. Benchmark requires running LLM servers.")
        logger.info("Expected to run: lm-eval-harness against vllm or ollama endpoints")


def main():
    parser = argparse.ArgumentParser(description="NEXUS Benchmark Runner")
    parser.add_argument("--config", default="nexus.yaml", help="Nexus config path")
    parser.add_argument("--benchmark", nargs="+", default=["mmlu", "humaneval", "math500"],
                        help="Benchmarks to run")
    parser.add_argument("--model", help="Specific model to test")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    if not config_path.exists():
        config_path = Path(__file__).parent.parent / "nexus.yaml"

    asyncio.run(run_benchmarks(str(config_path), args.benchmark))


if __name__ == "__main__":
    main()