#!/usr/bin/env python3
"""Regenerate ALL resource embeddings from current body_text + description, rebuild FAISS."""
import sys, os, sqlite3, json, time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "nexus/server"))
os.environ["TOKENIZERS_PARALLELISM"] = "false"

DB_PATH = str(Path.home() / "nexus/db/nexus.db")
INDEX_DIR = str(Path.home() / "nexus/db/faiss")
INDEX_FILE = os.path.join(INDEX_DIR, "resources.index")
REPORT_PATH = str(Path.home() / "nexus/server/scripts/faiss_reembed_report.json")

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

DIM = 384
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
BATCH_SIZE = 64

def run():
    ts = time.strftime("%Y%m%d_%H%M%S")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    # Get ALL active resources with their text (description + body_text)
    rows = conn.execute("""
        SELECT id, name, description, body_text, resource_type
        FROM resources
        WHERE status = 'active'
        ORDER BY id
    """).fetchall()

    # Build embedding text: prefer description + body_text for richest signal
    resource_ids = []
    texts = []
    for r in rows:
        rid, name, desc, body, rtype = r
        resource_ids.append(rid)
        # Build rich text for embedding: description + body_text (truncated)
        desc_text = (desc or "").strip()
        body_text = (body or "").strip()[:1000]  # First 1000 chars of body
        if not desc_text and not body_text:
            combined = name
        elif not body_text:
            combined = f"{name} {desc_text}"
        elif not desc_text:
            combined = f"{name} {body_text}"
        else:
            combined = f"{name} {desc_text} {body_text}"
        texts.append(combined[:2048])  # Safe truncation

    print(f"Resources to embed: {len(resource_ids)}")
    print(f"Model: {MODEL_ID}")

    # Compute embeddings
    model = SentenceTransformer(MODEL_ID)
    print("Computing embeddings...")
    embeddings = model.encode(texts, convert_to_numpy=True,
                              batch_size=BATCH_SIZE, show_progress_bar=True)
    print(f"Embeddings shape: {embeddings.shape}")

    # Normalize for cosine similarity (IndexFlatIP with normalized vectors)
    faiss.normalize_L2(embeddings.astype(np.float32))

    # Build FAISS index
    index = faiss.IndexFlatIP(DIM)
    index.add(embeddings.astype(np.float32))
    print(f"FAISS index built: {index.ntotal} vectors, dim={index.d}")

    # Save index
    os.makedirs(INDEX_DIR, exist_ok=True)
    faiss.write_index(index, INDEX_FILE)
    meta = {
        "resource_ids": resource_ids,
        "dim": DIM,
        "model": MODEL_ID,
        "count": len(resource_ids),
        "num_text_tokens": sum(len(t.split()) for t in texts),
        "ts": ts,
    }
    with open(INDEX_FILE + ".meta.json", "w") as f:
        json.dump(meta, f)
    print(f"FAISS index saved: {INDEX_FILE} ({len(resource_ids)} vectors)")

    # Update resource_embeddings table
    print("Updating resource_embeddings table...")
    conn.execute("DELETE FROM resource_embeddings")
    conn.executemany(
        "INSERT INTO resource_embeddings (resource_id, model_name, embedding, created_at) VALUES (?, ?, ?, ?)",
        [(rid, MODEL_ID, emb.astype(np.float32).tobytes(), ts)
         for rid, emb in zip(resource_ids, embeddings)]
    )
    conn.commit()
    print(f"Updated {len(resource_ids)} embeddings in DB")

    conn.close()

    # Report
    stats = {
        "total_embedded": len(resource_ids),
        "dim": DIM,
        "model": MODEL_ID,
        "index_path": INDEX_FILE,
        "index_vectors": index.ntotal,
        "embedding_shape": list(embeddings.shape),
        "ts": ts,
    }
    with open(REPORT_PATH, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"Report: {REPORT_PATH}")
    print("DONE - Embeddings regenerated with enriched text!")
    return stats

if __name__ == "__main__":
    run()
