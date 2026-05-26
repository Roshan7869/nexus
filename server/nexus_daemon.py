#!/usr/bin/env python3
"""
NEXUS MCP Sync Daemon — keeps MCP tools in sync with nexus.db

Based on: ScaleMCP CRUD pipeline (referenced in mcp_sync.py)
The MCPSyncManager code is complete but never ran — this is the daemon runner.

Usage:
  python -m server.nexus_daemon [--interval 60] [--once]
"""

import os, sys, time, signal, logging, json
from pathlib import Path

logger = logging.getLogger(__name__)

MCP_SYNC_ENABLED = os.environ.get("NEXUS_MCP_SYNC", "false").lower() == "true"
DEFAULT_INTERVAL = 60


def run_daemon(interval: int = DEFAULT_INTERVAL, once: bool = False):
    """Run MCP sync daemon."""
    sys.path.insert(0, str(Path.home() / 'nexus/server'))
    from mcp_sync import MCPSyncManager, MCPTool

    sync = MCPSyncManager(poll_interval=interval)

    # Startup self-check
    try:
        sync.conn.execute("SELECT 1").fetchone()
    except Exception as e:
        logger.error(f"MCP Sync startup failed: DB unavailable ({e})")
        sync.close()
        return

    logger.info(f"MCP Sync Daemon starting (interval={interval}s, PID={os.getpid()})")

    # Discover and sync known MCP servers
    def do_sync():
        # Try to connect to configured MCP servers
        # For now, self-check the DB state
        try:
            total = sync.conn.execute("SELECT COUNT(*) FROM resources WHERE resource_type='mcp'").fetchone()[0]
            logger.info(f"MCP sync check: {total} MCP tools in DB")
        except Exception as e:
            logger.warning(f"MCP sync check failed: {e}")

        # Check for stale tools (not synced in 24h)
        try:
            stale = sync.conn.execute("""
                SELECT COUNT(*) FROM resources
                WHERE resource_type='mcp' AND status='active'
                  AND updated_at < datetime('now', '-1 day')
            """).fetchone()[0]
            if stale > 0:
                logger.info(f"MCP sync: {stale} stale MCP tools detected")
        except Exception:
            pass

    do_sync()

    if once:
        sync.close()
        return

    # Background loop
    running = True
    def handler(signum, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)

    sync.start_background_sync()
    while running:
        try:
            time.sleep(5)
            if int(time.time()) % interval == 0:
                do_sync()
        except KeyboardInterrupt:
            break

    sync.stop_background_sync()
    sync.close()
    logger.info("MCP Sync Daemon stopped")


def get_sync_status() -> dict:
    """Get current MCP sync status."""
    sys.path.insert(0, str(Path.home() / 'nexus/server'))
    from mcp_sync import MCPSyncManager
    sync = MCPSyncManager()
    try:
        status = sync.get_sync_status()
        status["enabled"] = MCP_SYNC_ENABLED
        return status
    except Exception as e:
        return {"enabled": MCP_SYNC_ENABLED, "error": str(e)}
    finally:
        sync.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    import argparse
    parser = argparse.ArgumentParser(description="NEXUS MCP Sync Daemon")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                        help="Poll interval in seconds")
    parser.add_argument("--once", action="store_true",
                        help="Run sync once and exit")
    args = parser.parse_args()

    run_daemon(interval=args.interval, once=args.once)