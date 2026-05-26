#!/usr/bin/env python3
"""Generate embeddings for all resources that don't have them yet, then rebuild FAISS index."""
import sys
import os
import sqlite3
import time
from pathlib import Path

sys.path.insert(0, os.path.expanduser('~/nexus/server'))
sys.path.insert(0, os.path.expanduser('~/nexus'))

DB_PATH = Path.home() / 'nexus/db/nexus.db'

def generate_missing_embeddings():
    print("=" * 60)
    print("NEXUS Embedding Generation + FAISS Rebuild")
    print("=" * 60)
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    
    # Find resources missing embeddings
    missing = conn.execute("""
        SELECT r.id, r.name, COALESCE(r.description, '') || ' ' || COALESCE(SUBSTR(r.body_text, 1, 500), '')
        FROM resources r
        WHERE r.status = 'active'
        AND r.id NOT IN (SELECT DISTINCT resource_id FROM resource_embeddings)
    """).fetchall()
    
    print(f"Resources missing embeddings: {len(missing)}")
    
    if not missing:
        print("No missing embeddings — all resources already embedded!")
        conn.close()
        return
    
    # Load sentence-transformers model
    print("Loading all-MiniLM-L6-v2 model...")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer('all-MiniLM-L6-v2')
    print("Model loaded.")
    
    # Generate embeddings in batches
    batch_size = 64
    total_embedded = 0
    
    for batch_start in range(0, len(missing), batch_size):
        batch = missing[batch_start:batch_start + batch_size]
        ids = [row[0] for row in batch]
        texts = [row[2][:512] if row[2] else row[1] for row in batch]  # Truncate long texts
        
        # Generate embeddings
        embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        
        # Store in DB
        for i, (rid, emb_vec) in enumerate(zip(ids, embeddings)):
            import numpy as np
            emb_bytes = emb_vec.astype(np.float32).tobytes()
            # Upsert into resource_embeddings
            conn.execute("""
                INSERT OR REPLACE INTO resource_embeddings (resource_id, embedding, model_name, created_at)
                VALUES (?, ?, ?, datetime('now'))
            """, (rid, emb_bytes, 'all-MiniLM-L6-v2'))
            # Also update the resources.embedding column
            conn.execute("UPDATE resources SET embedding = ? WHERE id = ?", (emb_bytes, rid))
        
        total_embedded += len(batch)
        print(f"  Embedded {total_embedded}/{len(missing)} resources...")
    
    conn.commit()
    print(f"Total embeddings generated: {total_embedded}")
    
    # Verify
    final_missing = conn.execute("""
        SELECT COUNT(*) FROM resources r
        WHERE r.status = 'active'
        AND r.id NOT IN (SELECT DISTINCT resource_id FROM resource_embeddings)
    """).fetchone()[0]
    print(f"Remaining missing embeddings: {final_missing}")
    
    conn.close()
    
    # ── Rebuild FAISS index ──
    print("\nRebuilding FAISS index...")
    rebuild_faiss_index()

def rebuild_faiss_index():
    """Rebuild the FAISS index from all resource embeddings."""
    import faiss
    import numpy as np
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    
    # Load all embeddings
    rows = conn.execute("""
        SELECT re.resource_id, re.embedding
        FROM resource_embeddings re
        JOIN resources r ON r.id = re.resource_id
        WHERE r.status = 'active'
    """).fetchall()
    
    print(f"Loaded {len(rows)} embeddings for FAISS index")
    
    if not rows:
        print("No embeddings to index!")
        conn.close()
        return
    
    # Build numpy array
    ids = []
    vectors = []
    for rid, emb_bytes in rows:
        if emb_bytes is None:
            continue
        vec = np.frombuffer(emb_bytes, dtype=np.float32)
        if len(vec) == 384:  # all-MiniLM-L6-v2 dimension
            ids.append(rid)
            vectors.append(vec)
    
    if not vectors:
        print("No valid vectors found!")
        conn.close()
        return
    
    vectors_np = np.vstack(vectors).astype(np.float32)
    ids_np = np.array(ids, dtype=np.int64)
    
    # Create FAISS index
    dim = vectors_np.shape[1]
    index = faiss.IndexFlatIP(dim)  # Inner product for cosine similarity (after normalization)
    
    # Normalize vectors for cosine similarity
    faiss.normalize_L2(vectors_np)
    index.add(vectors_np)
    
    print(f"FAISS index: {index.ntotal} vectors, dimension {dim}")
    
    # Save index
    index_path = Path.home() / 'nexus/db/faiss_index.bin'
    faiss.write_index(index, str(index_path))
    print(f"FAISS index saved to {index_path}")
    
    # Also save ID mapping
    id_map_path = Path.home() / 'nexus/db/faiss_id_map.npy'
    np.save(str(id_map_path), ids_np)
    print(f"ID map saved to {id_map_path} ({len(ids)} entries)")
    
    # Update tool_attention_v4 module to reload
    print("\nFAISS index rebuilt successfully!")
    
    conn.close()

if __name__ == '__main__':
    generate_missing_embeddings()