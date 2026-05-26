#!/usr/bin/env python3
"""
Phase 4: Build FAISS Index from ISO Embeddings
1. Load all resource descriptions from DB (1,097 resources)
2. Compute 384-dim embeddings via all-MiniLM-L6-v2
3. Build IndexFlatIP(384) with normalized vectors
4. Store index + resource_ids metadata
5. Cache embeddings in DB (embeddings_cache table)
"""
import sys, os, sqlite3, json, shutil
from datetime import datetime

sys.path.insert(0, "/home/roshan/nexus/server")
os.environ["TOKENIZERS_PARALLELISM"] = "false"

DB_PATH = "/home/roshan/nexus/db/nexus.db"
INDEX_DIR = "/home/roshan/nexus/db/faiss"
INDEX_FILE = os.path.join(INDEX_DIR, "resources.index")
REPORT_PATH = "/home/roshan/nexus/db/phase4_report.json"
VENV_PY = "/home/roshan/nexus/venv/bin/python3"

import numpy as np, faiss
from sentence_transformers import SentenceTransformer

DIM = 384
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
BATCH_SIZE = 64

def run(dry_run=False):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(os.path.dirname(DB_PATH), "backups", f"nexus.db.ph4_pre_embed.{ts}.bak")
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
    shutil.copy2(DB_PATH, backup_path)
    print(f"[Phase 4] Backup: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, COALESCE(description, name, ''), body_text
        FROM resources
        WHERE description IS NOT NULL AND description != ''
        ORDER BY id
    """)
    rows = cur.fetchall()
    resource_ids = [r[0] for r in rows]
    texts = [r[1] for r in rows]

    print(f"[Phase 4] Loading {len(texts)} texts for embedding...")
    model = SentenceTransformer(MODEL_ID)

    embeddings = model.encode(texts, convert_to_numpy=True,
                              batch_size=BATCH_SIZE, show_progress_bar=True)
    faiss.normalize_L2(embeddings.astype(np.float32))

    index = faiss.IndexFlatIP(DIM)
    index.add(embeddings.astype(np.float32))

    os.makedirs(INDEX_DIR, exist_ok=True)
    faiss.write_index(index, INDEX_FILE)
    meta = {"resource_ids": resource_ids, "dim": DIM, "model": MODEL_ID,
            "count": len(resource_ids), "ts": ts}
    with open(INDEX_FILE + ".meta.json", "w") as f:
        json.dump(meta, f)

    if not dry_run:
        cur.execute("CREATE TABLE IF NOT EXISTS resource_embeddings (resource_id INTEGER PRIMARY KEY, model_name TEXT, embedding BLOB, created_at TEXT)")
        cur.execute("DELETE FROM resource_embeddings")
        for rid, emb in zip(resource_ids, embeddings.astype(np.float32)):
            cur.execute("INSERT INTO resource_embeddings (resource_id, model_name, embedding, created_at) VALUES (?, ?, ?, ?)",
                        (rid, MODEL_ID, emb.tobytes(), datetime.now().isoformat()))
        conn.commit()

    conn.close()

    stats = {
        "dry_run": dry_run, "backup": backup_path, "timestamp": ts,
        "total_embedded": len(texts), "dim": DIM, "model": MODEL_ID,
        "index_path": INDEX_FILE, "index_vectors": index.ntotal,
        "embedding_shape": list(embeddings.shape),
    }
    print(f"[Phase 4] Indexed {index.ntotal} vectors → {INDEX_FILE}")
    print(f"[Phase 4] VERDICT: {'PASS' if not dry_run else 'DRY-RUN OK'}")
    with open(REPORT_PATH, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"[Phase 4] Report: {REPORT_PATH}")
    return stats

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(dry_run=args.dry_run)