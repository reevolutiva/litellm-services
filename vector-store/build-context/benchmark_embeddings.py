#!/usr/bin/env python3
"""Measure embedding latency/throughput through the vector-store's LiteLLM path."""
import argparse
import asyncio
import json
import statistics
import time

from embedding_service import EmbeddingService
from config import settings


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=8)
    parser.add_argument("--text", default="A representative document for BGE-M3 embedding throughput measurement.")
    return parser.parse_args()


async def main():
    args = parse_args()
    if args.batch_size < 1 or args.repeats < 2:
        raise SystemExit("batch-size must be >= 1 and repeats must be >= 2")

    service = EmbeddingService()
    batch = [f"{args.text} item={index}" for index in range(args.batch_size)]
    await service.generate_embeddings(batch)  # warm-up

    durations = []
    dimension = None
    for _ in range(args.repeats):
        started = time.perf_counter()
        vectors = await service.generate_embeddings(batch)
        durations.append(time.perf_counter() - started)
        dimension = len(vectors[0])

    ordered = sorted(durations)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
    median = statistics.median(durations)
    print(json.dumps({
        "model": settings.embedding.model,
        "dimensions": dimension,
        "batch_size": args.batch_size,
        "repeats": args.repeats,
        "median_seconds": round(median, 6),
        "p95_seconds": round(ordered[p95_index], 6),
        "items_per_second": round(args.batch_size / median, 3),
        "durations_seconds": [round(value, 6) for value in durations],
    }, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
