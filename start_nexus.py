#!/usr/bin/env python3
"""
NEXUS v4 — Production startup script

Usage:
  python start_nexus.py                    # Start with safe defaults (all features OFF)
  python start_nexus.py --cluster          # Enable cluster routing
  python start_nexus.py --thompson          # Enable Thompson Sampling
  python start_nexus.py --reflection        # Enable self-reflection
  python start_nexus.py --mcp-sync          # Enable MCP sync daemon
  python start_nexus.py --all               # Enable ALL features
  python start_nexus.py --port 9000         # Custom port
"""
import os, sys, argparse, subprocess
from pathlib import Path

NEXUS_ROOT = Path.home() / 'nexus'

def main():
    parser = argparse.ArgumentParser(description="Start NEXUS v4 Unified Server")
    parser.add_argument("--cluster", action="store_true", help="Enable cluster routing")
    parser.add_argument("--thompson", action="store_true", help="Enable Thompson Sampling")
    parser.add_argument("--reflection", action="store_true", help="Enable self-reflection retry loop")
    parser.add_argument("--mcp-sync", action="store_true", help="Enable MCP sync daemon")
    parser.add_argument("--all", action="store_true", help="Enable ALL features")
    parser.add_argument("--port", type=int, default=8000, help="Port number (default: 8000)")
    parser.add_argument("--check-only", action="store_true", help="Just check dependencies, don't start")
    parser.add_argument("--mcp-only", action="store_true", help="Start only MCP server (no HTTP)")
    args = parser.parse_args()

    # ── Set feature flags ──────────────────────────────────────────────
    if args.all:
        args.cluster = True
        args.thompson = True
        args.reflection = True
        args.mcp_sync = True

    env = os.environ.copy()
    env["NEXUS_CLUSTER_ROUTING"] = "true" if args.cluster else "false"
    env["NEXUS_THOMPSON_ROUTING"] = "true" if args.thompson else "false"
    env["NEXUS_REFLECTION"] = "true" if args.reflection else "false"
    env["NEXUS_MCP_SYNC"] = "true" if args.mcp_sync else "false"
    env["NEXUS_PORT"] = str(args.port)

    # ── Check dependencies ─────────────────────────────────────────────
    print("NEXUS v4 — Unified Server")
    print("=" * 50)

    checks = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "httpx": "httpx",
        "pydantic": "pydantic",
        "pyyaml": "yaml",
        "sqlite3": "sqlite3",  # stdlib
    }

    missing = []
    for name, pkg in checks.items():
        if name == "sqlite3":
            try:
                import sqlite3
                print(f"  ✓ sqlite3 (stdlib)")
            except ImportError:
                print(f"  ✗ sqlite3 missing")
                missing.append("sqlite3")
        else:
            try:
                __import__(pkg)
                print(f"  ✓ {name}")
            except ImportError:
                print(f"  ✗ {name} missing")
                missing.append(name)

    if missing:
        print(f"\nMissing: {', '.join(missing)}")
        print("Install: pip install fastapi uvicorn httpx pydantic pyyaml")
        if args.check_only:
            return
        resp = input("Continue anyway? [y/N] ")
        if resp.lower() != 'y':
            sys.exit(1)

    # ── Check DB ────────────────────────────────────────────────────────
    db_path = NEXUS_ROOT / 'db/nexus.db'
    if db_path.exists():
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
        clusters = conn.execute("SELECT COUNT(DISTINCT cluster_name) FROM cluster_membership").fetchone()[0]
        beliefs = conn.execute("SELECT COUNT(*) FROM model_beliefs").fetchone()[0]
        conn.close()
        print(f"\n  Database: {count} resources, {clusters} clusters, {beliefs} model beliefs")
    else:
        print("\n  ⚠ Database not found — run migrations first")
        sys.exit(1)

    # ── Print config ────────────────────────────────────────────────────
    print(f"\n  Port:              {args.port}")
    print(f"  Cluster Routing:   {'ON' if args.cluster else 'OFF'}")
    print(f"  Thompson Sampling: {'ON' if args.thompson else 'OFF'}")
    print(f"  Self-Reflection:   {'ON' if args.reflection else 'OFF'}")
    print(f"  MCP Sync:          {'ON' if args.mcp_sync else 'OFF'}")
    print()

    if args.check_only:
        print("All checks passed.")
        return

    # ── Start MCP-only mode ─────────────────────────────────────────────
    if args.mcp_only:
        print("Starting MCP server (stdio)...")
        os.chdir(str(NEXUS_ROOT / 'server'))
        subprocess.run([sys.executable, "nexus-mcp-enhanced.py"], env=env)
        return

    # ── Start unified HTTP server ────────────────────────────────────────
    os.chdir(str(NEXUS_ROOT))
    os.execvpe(
        sys.executable,
        [sys.executable, "-m", "uvicorn", "server.unified:app",
         "--host", "0.0.0.0", "--port", str(args.port)],
        env
    )


if __name__ == "__main__":
    main()