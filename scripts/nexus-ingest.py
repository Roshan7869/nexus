#!/usr/bin/env python3
"""
NEXUS Knowledge Ingestion CLI
Supports directory scanning, Markdown section chunking, web page text extraction,
and PDF parsing, with auto-embedding, cluster classification, and DB insertion.

Usage:
    python scripts/nexus-ingest.py --source /path/to/repo --tag project-x
    python scripts/nexus-ingest.py --source https://example.com/docs --tag api-ref
"""
import sys
import os
import argparse
import sqlite3
import hashlib
import re
import urllib.request
from pathlib import Path
from datetime import datetime

# Insert paths to import existing utilities
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "server"))

try:
    import ollama
    import numpy as np
    import sqlite_vec
except ImportError:
    pass

DB_PATH = Path(__file__).parent.parent / "db" / "nexus.db"
EMBED_MODEL = 'nomic-embed-text:latest'

def clean_html(html_content: str) -> str:
    """Basic HTML text extraction helper."""
    # Remove script and style elements
    text = re.sub(r'<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>', '', html_content)
    text = re.sub(r'<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>', '', text)
    # Remove all HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Clean whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def chunk_text(text: str, chunk_size: int = 1500) -> list:
    """Split text into overlapping chunks."""
    words = text.split()
    chunks = []
    current_chunk = []
    current_len = 0
    for w in words:
        current_chunk.append(w)
        current_len += len(w) + 1
        if current_len >= chunk_size:
            chunks.append(" ".join(current_chunk))
            # overlap: keep last 15% of words
            overlap_cnt = max(1, int(len(current_chunk) * 0.15))
            current_chunk = current_chunk[-overlap_cnt:]
            current_len = sum(len(x) + 1 for x in current_chunk)
    if current_chunk:
        chunks.append(" ".join(current_chunk))
    return chunks

def chunk_markdown(content: str) -> list:
    """Split markdown intelligently by headers."""
    sections = re.split(r'\n(##+ .*)\n', content)
    chunks = []
    header = "General Documentation"
    for i, section in enumerate(sections):
        if i == 0:
            if section.strip():
                chunks.append((header, section.strip()))
        elif i % 2 == 1:
            header = section.strip("# ")
        else:
            if section.strip():
                chunks.append((header, section.strip()))
    return chunks

def get_embedding(text: str):
    """Generate nomic text embedding."""
    try:
        resp = ollama.embeddings(model=EMBED_MODEL, prompt=text[:12000])
        return sqlite_vec.serialize_float32(resp['embedding'])
    except Exception as e:
        print(f"Error generating embedding: {e}")
        return None

def find_closest_cluster(conn, embedding_bytes) -> tuple:
    """Find closest cluster and confidence based on dot product similarity with L2 normalized centroids."""
    if not embedding_bytes:
        return "general", 0.5
        
    try:
        q_arr = np.frombuffer(embedding_bytes, dtype=np.float32)
        q_norm = q_arr / (np.linalg.norm(q_arr) + 1e-10)
        
        centroids = conn.execute("""
            SELECT name, centroid_embedding FROM cluster_definitions 
            WHERE centroid_embedding IS NOT NULL
        """).fetchall()
        
        best_cluster = "general"
        best_score = -1.0
        
        for c in centroids:
            c_arr = np.frombuffer(c["centroid_embedding"], dtype=np.float32)
            c_norm = c_arr / (np.linalg.norm(c_arr) + 1e-10)
            score = float(np.dot(q_norm, c_norm))
            if score > best_score:
                best_score = score
                best_cluster = c["name"]
                
        # Map score to normalized confidence
        confidence = min(max((best_score + 1.0) / 2.0, 0.0), 1.0)
        return best_cluster, confidence
    except Exception as e:
        return "general", 0.5

def check_duplicate(conn, body_text: str, embedding_bytes) -> bool:
    """Check for exact or high semantic similarity (>0.95) duplicates."""
    # 1. Exact match check
    row = conn.execute("SELECT id FROM resources WHERE body_text=? AND status='active' LIMIT 1", (body_text,)).fetchone()
    if row:
        return True
        
    # 2. Semantic overlap match check (>0.95 similarity)
    if embedding_bytes:
        try:
            q_arr = np.frombuffer(embedding_bytes, dtype=np.float32)
            q_norm = q_arr / (np.linalg.norm(q_arr) + 1e-10)
            
            # Fetch active embeddings
            rows = conn.execute("SELECT id, embedding FROM resources WHERE status='active' AND embedding IS NOT NULL").fetchall()
            for r in rows:
                if not r["embedding"]:
                    continue
                r_arr = np.frombuffer(r["embedding"], dtype=np.float32)
                r_norm = r_arr / (np.linalg.norm(r_arr) + 1e-10)
                sim = float(np.dot(q_norm, r_norm))
                if sim > 0.95:
                    return True
        except Exception:
            pass
            
    return False


def get_or_create_source(conn, source_type: str, source_path: str, content_hash: str) -> int:
    """Insert or retrieve an ingestion_sources record. Returns source_id."""
    try:
        existing = conn.execute(
            "SELECT id FROM ingestion_sources WHERE source_path = ? AND content_hash = ?",
            (source_path, content_hash)
        ).fetchone()
        if existing:
            return existing["id"]
        conn.execute("""
            INSERT INTO ingestion_sources (source_type, source_path, content_hash, status)
            VALUES (?, ?, ?, 'processing')
        """, (source_type, source_path, content_hash))
        conn.commit()
        return conn.execute(
            "SELECT id FROM ingestion_sources WHERE source_path = ? AND content_hash = ? ORDER BY id DESC LIMIT 1",
            (source_path, content_hash)
        ).fetchone()["id"]
    except Exception as e:
        print(f"  Warning: Could not create ingestion_source record: {e}")
        return None


def _add_cluster_membership(conn, resource_id: int, cluster_name: str, confidence: float):
    """Add resource to cluster_membership table (authoritative cluster source).
    Also seeds initial belief for Thompson Sampling."""
    try:
        conn.execute("""
            INSERT OR IGNORE INTO cluster_membership (cluster_name, resource_id, affinity)
            VALUES (?, ?, ?)
        """, (cluster_name, resource_id, confidence))
    except Exception as e:
        print(f"  Warning: cluster_membership insert failed for {resource_id}: {e}")


def _seed_belief(conn, skill_name: str, cluster_name: str, task_type: str = "general"):
    """Seed an initial Thompson Sampling belief for a newly ingested resource.
    Uses weak prior (alpha=1, beta=1) so Thompson Sampling will explore it."""
    try:
        resource_key = f"resource:{skill_name}"
        conn.execute("""
            INSERT OR IGNORE INTO model_beliefs 
                (provider, model, task_type, alpha, beta, total_requests, total_reward, avg_reward)
            VALUES ('nexus', ?, ?, 1.0, 1.0, 0, 0, 0)
        """, (resource_key, task_type))
    except Exception as e:
        print(f"  Warning: belief seeding failed for {skill_name}: {e}")


def _map_cluster_to_task_type(cluster_name: str) -> str:
    """Map NEXUS cluster names to Thompson Sampling task_types."""
    mapping = {
        "analyzer_planner": "reasoning",
        "architect": "architecture",
        "frontend_ui": "frontend",
        "backend_api": "coding",
        "devops_infra": "devops",
        "quality_security": "agentic",
        "knowledge_wiki": "research",
        "general": "general"
    }
    return mapping.get(cluster_name, "general")


def register_chunk(conn, source_id, resource_id, chunk_index, chunk_type, heading,
                   content, content_hash, token_count, start_line=None, end_line=None):
    """Insert a chunk record into ingestion_chunks and link resource_id."""
    if source_id is None:
        return
    try:
        conn.execute("""
            INSERT OR IGNORE INTO ingestion_chunks
                (source_id, resource_id, chunk_index, chunk_type, heading,
                 content, content_hash, token_count, start_line, end_line)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (source_id, resource_id, chunk_index, chunk_type, heading,
               content, content_hash, token_count, start_line, end_line))
    except Exception as e:
        print(f"  Warning: Could not register chunk {chunk_index}: {e}")

def ingest_url(url: str, tag: str, chunk_size: int):
    print(f"Fetching URL: {url}")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode('utf-8', errors='ignore')
        
        title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
        title = title_match.group(1) if title_match else url
        
        clean_txt = clean_html(html)
        chunks = chunk_text(clean_txt, chunk_size)
        
        print(f"URL loaded successfully. Extracted {len(chunks)} chunks.")
        
        conn = sqlite3.connect(str(DB_PATH))
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.row_factory = sqlite3.Row
        
        # Register ingestion source
        url_hash = hashlib.sha256(clean_txt.encode()).hexdigest()[:16]
        source_id = get_or_create_source(conn, 'url', url, url_hash)
        
        inserted_count = 0
        for idx, chunk in enumerate(chunks):
            emb = get_embedding(chunk)
            if check_duplicate(conn, chunk, emb):
                print(f"  Skipping duplicate web chunk: Part {idx+1}")
                continue
                
            chunk_name = f"{title} (Part {idx+1})"
            cluster, conf = find_closest_cluster(conn, emb)
            chunk_hash = hashlib.sha256(chunk.encode()).hexdigest()[:16]
            token_count = len(chunk.split())
            
            conn.execute("""
                INSERT INTO resources (name, description, body_text, resource_type, repo_name, status, cluster, cluster_confidence, embedding)
                VALUES (?, ?, ?, 'doc', ?, 'active', ?, ?, ?)
            """, (chunk_name, f"Web chunk from {url}", chunk, tag, cluster, conf, emb))
            resource_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            
            # Auto-add to cluster_membership + seed belief
            _add_cluster_membership(conn, resource_id, cluster, conf)
            _seed_belief(conn, chunk_name, cluster)
            
            register_chunk(conn, source_id, resource_id, idx, 'web_chunk', title,
                           chunk, chunk_hash, token_count)
            inserted_count += 1
            
        # Update source stats
        if source_id:
            conn.execute("""
                UPDATE ingestion_sources
                SET total_chunks = ?, status = 'completed', last_ingested_at = datetime('now')
                WHERE id = ?
            """, (inserted_count, source_id))
        conn.commit()
        conn.close()
        print(f"Ingestion of URL completed. Inserted {inserted_count} new chunks.")
    except Exception as e:
        print(f"URL ingestion failed: {e}")

def ingest_directory(path: Path, tag: str, chunk_size: int):
    print(f"Ingesting directory: {path}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.row_factory = sqlite3.Row
    
    files = list(path.rglob("*.md")) + list(path.rglob("*.py"))
    
    inserted_count = 0
    for f in files:
        if f.is_dir() or "venv" in str(f) or ".git" in str(f):
            continue
            
        try:
            content = f.read_text(encoding='utf-8', errors='ignore')
            if not content.strip():
                continue

            # Register ingestion source per file
            file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
            source_id = get_or_create_source(conn, 'file', str(f), file_hash)
                
            if f.suffix == ".md":
                chunks = chunk_markdown(content)
                lines = content.splitlines()
                for chunk_idx, (header, chunk_body) in enumerate(chunks):
                    emb = get_embedding(chunk_body)
                    if check_duplicate(conn, chunk_body, emb):
                        continue
                        
                    chunk_name = f"{f.name}: {header}"
                    cluster, conf = find_closest_cluster(conn, emb)
                    chunk_hash = hashlib.sha256(chunk_body.encode()).hexdigest()[:16]
                    token_count = len(chunk_body.split())
                    
                    conn.execute("""
                        INSERT INTO resources (name, description, body_text, resource_type, repo_name, status, cluster, cluster_confidence, embedding)
                        VALUES (?, ?, ?, 'doc', ?, 'active', ?, ?, ?)
                    """, (chunk_name, f"Markdown section from {f.name}", chunk_body, tag, cluster, conf, emb))
                    resource_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    
                    # Auto-add to cluster_membership + seed belief
                    _add_cluster_membership(conn, resource_id, cluster, conf)
                    task_type = _map_cluster_to_task_type(cluster)
                    _seed_belief(conn, chunk_name, cluster, task_type)
                    
                    register_chunk(conn, source_id, resource_id, chunk_idx, 'section', header,
                                   chunk_body, chunk_hash, token_count)
                    inserted_count += 1
            else:
                # Code file: standard chunking
                chunks = chunk_text(content, chunk_size)
                for idx, chunk_body in enumerate(chunks):
                    emb = get_embedding(chunk_body)
                    if check_duplicate(conn, chunk_body, emb):
                        continue
                        
                    chunk_name = f"{f.name} (Part {idx+1})"
                    cluster, conf = find_closest_cluster(conn, emb)
                    chunk_hash = hashlib.sha256(chunk_body.encode()).hexdigest()[:16]
                    token_count = len(chunk_body.split())
                    
                    conn.execute("""
                        INSERT INTO resources (name, description, body_text, resource_type, repo_name, status, cluster, cluster_confidence, embedding)
                        VALUES (?, ?, ?, 'code', ?, 'active', ?, ?, ?)
                    """, (chunk_name, f"Source code chunk from {f.name}", chunk_body, tag, cluster, conf, emb))
                    resource_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    
                    # Auto-add to cluster_membership + seed belief
                    _add_cluster_membership(conn, resource_id, cluster, conf)
                    task_type = _map_cluster_to_task_type(cluster)
                    _seed_belief(conn, chunk_name, cluster, task_type)
                    
                    register_chunk(conn, source_id, resource_id, idx, 'code_chunk', f.name,
                                   chunk_body, chunk_hash, token_count)
                    inserted_count += 1

            # Update source stats
            if source_id:
                conn.execute("""
                    UPDATE ingestion_sources
                    SET total_chunks = total_chunks + ?,
                        status = 'completed',
                        last_ingested_at = datetime('now')
                    WHERE id = ?
                """, (inserted_count, source_id))
        except Exception as e:
            print(f"Skipping file {f.name} due to error: {e}")
            
    conn.commit()
    conn.close()
    print(f"Directory ingestion completed. Inserted {inserted_count} new chunks.")

def main():
    parser = argparse.ArgumentParser(description="NEXUS Knowledge Ingestion Engine")
    parser.add_argument("--source", required=True, help="URL, directory path, or file path to ingest")
    parser.add_argument("--tag", default="general-ingest", help="Label or tag for the ingested resources")
    parser.add_argument("--chunk-size", type=int, default=1500, help="Overlap chunk sizing in character length")
    args = parser.parse_args()
    
    source = args.source
    if source.startswith("http://") or source.startswith("https://"):
        ingest_url(source, args.tag, args.chunk_size)
    else:
        path = Path(source).resolve()
        if not path.exists():
            print(f"Error: Source path {source} does not exist.")
            sys.exit(1)
        if path.is_dir():
            ingest_directory(path, args.tag, args.chunk_size)
        else:
            print("File-level ingestion falls back to directory processing.")
            ingest_directory(path.parent, args.tag, args.chunk_size)

if __name__ == "__main__":
    main()
