-- 011: source_file_path on ingestion_chunks
-- Decouples chunk→resource linking from content_hash so path-based linking
-- recovers hash-drifted chunks (F1a value-ceiling fix). Backfill is done by
-- server/ingest/linker.py:link_source_file_paths() which re-walks each
-- local_dir source and sets source_file_path by (source_id, content_hash),
-- with a (source_id, chunk_index) fallback for drifted chunks.
PRAGMA journal_mode=WAL;

ALTER TABLE ingestion_chunks ADD COLUMN source_file_path TEXT;

CREATE INDEX IF NOT EXISTS idx_chunks_source_file
    ON ingestion_chunks(source_id, source_file_path);