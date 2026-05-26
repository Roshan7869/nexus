#!/usr/bin/env python3
"""
Seed realistic benchmark results for NEXUS models into sqlite database.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "db" / "nexus.db"

BENCHMARK_DATA = [
    # DeepSeek Reasoning Models
    ("ollama_cloud", "deepseek-v4-pro:cloud", "mmlu", 0.885, 100),
    ("ollama_cloud", "deepseek-v4-pro:cloud", "humaneval", 0.915, 100),
    ("ollama_cloud", "deepseek-v4-pro:cloud", "math500", 0.902, 100),
    ("ollama_cloud", "deepseek-v4-pro:cloud", "arc", 0.940, 100),
    
    ("ollama_cloud", "deepseek-v4-flash:cloud", "mmlu", 0.812, 100),
    ("ollama_cloud", "deepseek-v4-flash:cloud", "humaneval", 0.845, 100),
    ("ollama_cloud", "deepseek-v4-flash:cloud", "math500", 0.750, 100),
    ("ollama_cloud", "deepseek-v4-flash:cloud", "arc", 0.880, 100),

    # Qwen Coder Models
    ("ollama_cloud", "qwen3-coder:480b-cloud", "mmlu", 0.850, 100),
    ("ollama_cloud", "qwen3-coder:480b-cloud", "humaneval", 0.924, 100),
    ("ollama_cloud", "qwen3-coder:480b-cloud", "math500", 0.810, 100),
    ("ollama_cloud", "qwen3-coder:480b-cloud", "arc", 0.905, 100),
    
    ("nvidia_nim", "qwen/qwen3-coder-480b-a35b-instruct", "mmlu", 0.848, 100),
    ("nvidia_nim", "qwen/qwen3-coder-480b-a35b-instruct", "humaneval", 0.920, 100),
    ("nvidia_nim", "qwen/qwen3-coder-480b-a35b-instruct", "math500", 0.805, 100),
    ("nvidia_nim", "qwen/qwen3-coder-480b-a35b-instruct", "arc", 0.900, 100),

    # LLaMA 3.3 Models
    ("nvidia_nim", "meta/llama-3.3-70b-instruct", "mmlu", 0.825, 100),
    ("nvidia_nim", "meta/llama-3.3-70b-instruct", "humaneval", 0.810, 100),
    ("nvidia_nim", "meta/llama-3.3-70b-instruct", "math500", 0.710, 100),
    ("nvidia_nim", "meta/llama-3.3-70b-instruct", "arc", 0.890, 100),

    # Gemini & Gemma Models
    ("ollama_cloud", "gemini-3-flash-preview:cloud", "mmlu", 0.805, 100),
    ("ollama_cloud", "gemini-3-flash-preview:cloud", "humaneval", 0.790, 100),
    ("ollama_cloud", "gemini-3-flash-preview:cloud", "math500", 0.680, 100),
    ("ollama_cloud", "gemini-3-flash-preview:cloud", "arc", 0.865, 100),

    ("ollama_cloud", "gemma4:31b-cloud", "mmlu", 0.795, 100),
    ("ollama_cloud", "gemma4:31b-cloud", "humaneval", 0.720, 100),
    ("ollama_cloud", "gemma4:31b-cloud", "math500", 0.620, 100),
    ("ollama_cloud", "gemma4:31b-cloud", "arc", 0.840, 100),

    # Cogito (Reasoning) Models
    ("ollama_cloud", "cogito-2.1:671b-cloud", "mmlu", 0.875, 100),
    ("ollama_cloud", "cogito-2.1:671b-cloud", "humaneval", 0.890, 100),
    ("ollama_cloud", "cogito-2.1:671b-cloud", "math500", 0.860, 100),
    ("ollama_cloud", "cogito-2.1:671b-cloud", "arc", 0.920, 100),

    # Mistral Models
    ("ollama_cloud", "mistral-large-3:675b-cloud", "mmlu", 0.845, 100),
    ("ollama_cloud", "mistral-large-3:675b-cloud", "humaneval", 0.830, 100),
    ("ollama_cloud", "mistral-large-3:675b-cloud", "math500", 0.730, 100),
    ("ollama_cloud", "mistral-large-3:675b-cloud", "arc", 0.895, 100),
]

def main():
    print(f"Connecting to database: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Ensure schema exists (table check is done during migrations but safety first)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS benchmark_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        benchmark TEXT NOT NULL,
        score REAL NOT NULL,
        samples INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(provider, model, benchmark)
    )
    """)
    
    print(f"Seeding {len(BENCHMARK_DATA)} benchmark results...")
    
    seeded_count = 0
    for provider, model, benchmark, score, samples in BENCHMARK_DATA:
        cursor.execute("""
            INSERT OR REPLACE INTO benchmark_results (provider, model, benchmark, score, samples)
            VALUES (?, ?, ?, ?, ?)
        """, (provider, model, benchmark, score, samples))
        seeded_count += 1
        
    conn.commit()
    conn.close()
    print(f"Successfully seeded {seeded_count} benchmark results in benchmark_results table!")

if __name__ == "__main__":
    main()
