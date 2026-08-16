#!/usr/bin/env python3
"""TurboQuant spike — training-free vector quantization on REAL NEXUS vectors.

Per arXiv 2504.19874: random rotation (spreads outliers; Hadamard when dim is
a power of two — 384 isn't, so we use a seeded random orthogonal matrix, same
outlier-annihilating property) + per-vector scalar quantization at 8/4/2 bits.
Measures recall@10 vs float32 and bytes/vector on the resources.embedding
matrix. ADOPT-IF: recall@10 >= 0.95 at 4 bits AND memory < 40% of float32.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
DB = REPO_ROOT / "db" / "nexus.db"


def load_matrix() -> np.ndarray:
    conn = sqlite3.connect(str(DB))
    blobs = [r[0] for r in conn.execute(
        "SELECT embedding FROM resources WHERE embedding IS NOT NULL")]
    conn.close()
    # dims are mixed in the wild — keep the dominant length only
    from collections import Counter
    want = Counter(len(b) for b in blobs).most_common(1)[0][0]
    blobs = [b for b in blobs if len(b) == want]
    v = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(len(blobs), -1)
    v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)  # unit → dot = cosine
    return v.astype(np.float32)


def encode(v: np.ndarray, bits: int, seed: int = 42):
    d = v.shape[1]
    rng = np.random.default_rng(seed)
    q, _ = np.linalg.qr(rng.standard_normal((d, d)))  # random orthogonal rotation
    vr = v @ q.astype(np.float32)
    levels = 2 ** (bits - 1) - 1
    scale = np.abs(vr).max(axis=1, keepdims=True)
    codes = np.clip(np.rint(vr / (scale + 1e-9) * levels), -levels, levels)
    return codes, scale.astype(np.float16), q.astype(np.float32)


def recall_at_k(v: np.ndarray, codes: np.ndarray, scale: np.ndarray, q: np.ndarray,
                k: int = 10, sample: int = 200) -> float:
    # dequant: codes * scale / levels, then rotate back (q orthogonal → q.T)
    levels = max(1.0, float(np.abs(codes).max()))
    deq = codes.astype(np.float32) * (scale.astype(np.float32) / levels)
    deq = deq @ q.T
    rng = np.random.default_rng(7)
    idx = rng.choice(len(v), size=min(sample, len(v)), replace=False)
    hits = 0
    for i in idx:
        gt = set(np.argsort(-(v @ v[i]))[1 : k + 1].tolist())       # excl. self
        got = set(np.argsort(-(deq @ v[i]))[1 : k + 1].tolist())
        hits += len(gt & got) / k
    return hits / len(idx)


def main() -> int:
    v = load_matrix()
    d = v.shape[1]
    print(f"vectors={v.shape[0]} dim={d}")
    print(f"{'bits':>4} {'recall@10':>10} {'bytes/vec':>10} {'%float32':>9}")
    fp32_bpv = d * 4
    for bits in (8, 4, 2):
        codes, scale, q = encode(v, bits)
        r = recall_at_k(v, codes, scale, q)
        if bits == 8:
            bpv = d + 2  # int8 + fp16 scale
        else:
            # packed sub-byte codes + fp16 scale (ideal packing)
            bpv = d * bits / 8 + 2
        print(f"{bits:>4} {r:>10.4f} {bpv:>10.1f} {100 * bpv / fp32_bpv:>8.1f}%")
    verdict_4bit = None  # filled by eyeball into the memo; spike prints numbers
    return 0


if __name__ == "__main__":
    sys.exit(main())
