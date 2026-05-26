"""Generate embeddings for the 7 graphify MCP tools in NEXUS."""
import sqlite3
import numpy as np
from datetime import datetime
from sentence_transformers import SentenceTransformer

model = SentenceTransformer('all-MiniLM-L6-v2')
conn = sqlite3.connect('/home/roshan/nexus/db/nexus.db')
cur = conn.cursor()

cur.execute("SELECT id, name, description FROM resources WHERE repo_name='graphify'")
rows = cur.fetchall()

for rid, name, desc in rows:
    emb = model.encode(f'{name}: {desc}')
    emb = emb / np.linalg.norm(emb)
    eb = emb.astype(np.float32).tobytes()
    cur.execute(
        'INSERT OR REPLACE INTO resource_embeddings (resource_id, model_name, embedding, created_at) VALUES (?,?,?,?)',
        (rid, 'all-MiniLM-L6-v2', eb, datetime.now().isoformat())
    )
    cur.execute('UPDATE resources SET embedding=? WHERE id=?', (eb, rid))
    print(f'  Embedded: {name}')

conn.commit()
print(f'Done - {len(rows)} graphify tools embedded')
conn.close()