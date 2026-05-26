#!/usr/bin/env python3
"""
mcp_sync.py — NEXUS v2 Dynamic MCP Sync
Based on: ScaleMCP CRUD pipeline with MCP servers as source of truth

CRUD Operations:
- CREATE: New tool detected → Add to vector index
- READ:   Tool retrieved → Merge with MCP schema
- UPDATE: Tool schema changed → Re-index
- DELETE: Tool removed → Remove from index
"""

import json, sqlite3, threading, time, os
from pathlib import Path
from typing import Dict, List, Optional, Callable
from datetime import datetime
from dataclasses import dataclass
from enum import Enum

DB_PATH = Path(os.environ.get("NEXUS_DB_PATH", str(Path.home() / 'nexus/db/nexus.db')))

class SyncOperation(Enum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"

@dataclass
class MCPTool:
    name: str
    description: str
    schema: str
    mcp_server: str
    tags: List[str] = None
    
    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'description': self.description,
            'schema': self.schema,
            'mcp_server': self.mcp_server,
            'tags': self.tags or []
        }

class MCPSyncManager:
    """
    Dynamic MCP tool synchronization based on ScaleMCP paper.
    
    Features:
    - Real-time sync with MCP servers (CRUD operations)
    - Debounced updates to prevent flooding
    - Conflict resolution (MCP server = source of truth)
    - Background sync thread
    """
    
    def __init__(self, poll_interval: int = 60):
        self.conn = self._get_conn()
        self.poll_interval = poll_interval
        self.sync_thread = None
        self.running = False
        self.event_callbacks: List[Callable] = []
        self.pending_syncs: List[Dict] = []
        self.sync_lock = threading.Lock()
        
        # Track MCP server state
        self.mcp_servers: Dict[str, Dict] = {}
        
    def _get_conn(self):
        conn = sqlite3.connect(str(DB_PATH), timeout=60)
        conn.row_factory = sqlite3.Row
        return conn
    
    # ═══════════════════════════════════════════════════════════════════
# CRUD Operations
    # ═══════════════════════════════════════════════════════════════════

    def create_tool(self, tool: MCPTool) -> bool:
        """CREATE: Add new tool to index"""
        try:
            existing = self.conn.execute("""
                SELECT name FROM resources WHERE name = ? AND resource_type = 'mcp'
            """, (tool.name,)).fetchone()
            
            if existing:
                return self.update_tool(tool)
            
            self.conn.execute("""
                INSERT INTO resources(name, description, body_text, resource_type,
                                     repo_name, file_path, status, tier,
                                     times_triggered, times_correct, times_wrong,
                                     embedding)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tool.name,
                tool.description,
                tool.schema,
                'mcp',
                tool.mcp_server,
                f"/mcp/{tool.mcp_server}/{tool.name}.json",
                'active',
                2,
                0, 0, 0,
                b''
            ))
            
            self.conn.commit()
            self._log_sync(SyncOperation.CREATE, tool.name)
            return True
            
        except Exception as e:
            print(f"CREATE failed: {e}")
            return False
    
    def read_tool(self, tool_name: str) -> Optional[MCPTool]:
        """READ: Retrieve and merge MCP tool with schema"""
        row = self.conn.execute("""
            SELECT * FROM resources WHERE name = ? AND resource_type = 'mcp'
        """, (tool_name,)).fetchone()
        
        if not row:
            return None
        
        return MCPTool(
            name=row['name'],
            description=row['description'] or '',
            schema=row['body_text'] or '{}',
            mcp_server=row['repo_name'] or ''
        )
    
    def update_tool(self, tool: MCPTool) -> bool:
        """UPDATE: Re-index tool with new schema"""
        try:
            self.conn.execute("""
                UPDATE resources
                SET description = ?,
                    body_text = ?,
                    repo_name = ?,
                    updated_at = datetime('now')
                WHERE name = ? AND resource_type = 'mcp'
            """, (
                tool.description,
                tool.schema,
                tool.mcp_server,
                tool.name
            ))
            
            self.conn.commit()
            self._log_sync(SyncOperation.UPDATE, tool.name)
            return True
            
        except Exception as e:
            print(f"UPDATE failed: {e}")
            return False
    
    def delete_tool(self, tool_name: str) -> bool:
        """DELETE: Remove tool from index"""
        try:
            # Soft delete - mark as archived
            self.conn.execute("""
                UPDATE resources
                SET status = 'archived',
                    updated_at = datetime('now')
                WHERE name = ? AND resource_type = 'mcp'
            """, (tool_name,))
            
            self.conn.commit()
            self._log_sync(SyncOperation.DELETE, tool_name)
            return True
            
        except Exception as e:
            print(f"DELETE failed: {e}")
            return False
    
    # ═══════════════════════════════════════════════════════════════════
    # Sync Pipeline
    # ═══════════════════════════════════════════════════════════════════
    
    def sync_from_mcp_server(self, server_name: str, tools: List[MCPTool]) -> Dict:
        """
        Sync tools from an MCP server.
        
        Uses CRUD operations:
        - Tools in server but not in DB → CREATE
        - Tools in both → READ + check for UPDATE
        - Tools in DB but not in server → DELETE
        """
        existing = self.conn.execute("""
            SELECT name FROM resources
            WHERE repo_name = ? AND resource_type = 'mcp'
        """, (server_name,)).fetchall()
        
        existing_names = {r['name'] for r in existing}
        server_names = {t.name for t in tools}
        
        results = {'created': [], 'updated': [], 'deleted': [], 'errors': []}
        
        # CREATE new tools
        for tool in tools:
            if tool.name not in existing_names:
                if self.create_tool(tool):
                    results['created'].append(tool.name)
                else:
                    results['errors'].append(f"CREATE: {tool.name}")
        
        # Check for updates
        for tool in tools:
            if tool.name in existing_names:
                # Check if schema changed
                existing_tool = self.read_tool(tool.name)
                if existing_tool and self._schema_changed(existing_tool, tool):
                    if self.update_tool(tool):
                        results['updated'].append(tool.name)
                    else:
                        results['errors'].append(f"UPDATE: {tool.name}")
        
        # DELETE removed tools
        for name in existing_names:
            if name not in server_names:
                if self.delete_tool(name):
                    results['deleted'].append(name)
                else:
                    results['errors'].append(f"DELETE: {name}")
        
        self._log_sync_batch(server_name, results)
        return results
    
    def _schema_changed(self, old: MCPTool, new: MCPTool) -> bool:
        """Check if tool schema has changed"""
        return old.description != new.description or old.schema != new.schema
    
    # ═══════════════════════════════════════════════════════════════════
    # Background Sync
    # ═══════════════════════════════════════════════════════════════════
    
    def start_background_sync(self):
        """Start background sync thread"""
        if self.running:
            return
        
        self.running = True
        self.sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self.sync_thread.start()
    
    def stop_background_sync(self):
        """Stop background sync"""
        self.running = False
        if self.sync_thread:
            self.sync_thread.join(timeout=5)
    
    def _sync_loop(self):
        """Background sync loop"""
        while self.running:
            try:
                self._process_pending_syncs()
            except Exception as e:
                print(f"Sync loop error: {e}")
            
            time.sleep(self.poll_interval)
    
    def queue_sync(self, operation: SyncOperation, tool: MCPTool):
        """Queue a sync operation (debounced)"""
        with self.sync_lock:
            self.pending_syncs.append({
                'operation': operation,
                'tool': tool,
                'timestamp': datetime.now().isoformat()
            })
    
    def _process_pending_syncs(self):
        """Process queued sync operations"""
        with self.sync_lock:
            if not self.pending_syncs:
                return
            
            # Debounce: group operations for same tool
            tool_ops = {}
            for sync in self.pending_syncs:
                tool_name = sync['tool'].name
                if tool_name not in tool_ops:
                    tool_ops[tool_name] = sync
                else:
                    # Keep latest operation
                    if sync['timestamp'] > tool_ops[tool_name]['timestamp']:
                        tool_ops[tool_name] = sync
            
            self.pending_syncs.clear()
        
        # Process deduplicated operations
        for tool_name, sync in tool_ops.items():
            if sync['operation'] == SyncOperation.CREATE:
                self.create_tool(sync['tool'])
            elif sync['operation'] == SyncOperation.UPDATE:
                self.update_tool(sync['tool'])
            elif sync['operation'] == SyncOperation.DELETE:
                self.delete_tool(tool_name)
    
    # ═══════════════════════════════════════════════════════════════════
    # Logging & Monitoring
    # ═══════════════════════════════════════════════════════════════════
    
    def _log_sync(self, operation: SyncOperation, tool_name: str):
        """Log individual sync operation"""
        try:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS mcp_sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT,
                    tool_name TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            self.conn.execute("""
                INSERT INTO mcp_sync_log(operation, tool_name)
                VALUES(?, ?)
            """, (operation.value, tool_name))
            self.conn.commit()
        except:
            pass
    
    def _log_sync_batch(self, server: str, results: Dict):
        """Log batch sync results"""
        try:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS mcp_sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT,
                    tool_name TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            for name in results['created']:
                self.conn.execute("INSERT INTO mcp_sync_log VALUES(NULL,?,?,?)",
                                (SyncOperation.CREATE.value, name))
            for name in results['updated']:
                self.conn.execute("INSERT INTO mcp_sync_log VALUES(NULL,?,?,?)",
                                (SyncOperation.UPDATE.value, name))
            for name in results['deleted']:
                self.conn.execute("INSERT INTO mcp_sync_log VALUES(NULL,?,?,?)",
                                (SyncOperation.DELETE.value, name))
            
            self.conn.commit()
        except:
            pass
    
    def get_sync_status(self) -> Dict:
        """Get current sync status"""
        try:
            total = self.conn.execute("""
                SELECT COUNT(*) FROM mcp_sync_log
            """).fetchone()[0]
            
            by_op = self.conn.execute("""
                SELECT operation, COUNT(*) FROM mcp_sync_log GROUP BY operation
            """).fetchall()
            
            return {
                'total_operations': total,
                'by_operation': {op: cnt for op, cnt in by_op},
                'pending_syncs': len(self.pending_syncs),
                'running': self.running
            }
        except:
            return {'error': 'Unable to get sync status'}
    
    def close(self):
        self.stop_background_sync()
        self.conn.close()


def demo():
    sync = MCPSyncManager()
    
    print("="*60)
    print("  MCP SYNC DEMO")
    print("="*60)
    
    # Create a sample MCP tool
    tool = MCPTool(
        name="github-issue-creator",
        description="Create GitHub issues programmatically",
        schema='{"title": "string", "body": "string", "labels": "array"}',
        mcp_server="github-mcp"
    )
    
    # CRUD operations
    print("\n📦 CREATE tool:")
    result = sync.create_tool(tool)
    print(f"   Success: {result}")
    
    print("\n📖 READ tool:")
    retrieved = sync.read_tool("github-issue-creator")
    if retrieved:
        print(f"   Name: {retrieved.name}")
        print(f"   Server: {retrieved.mcp_server}")
    
    print("\n📝 UPDATE tool:")
    tool.description = "Create GitHub issues with labels"
    result = sync.update_tool(tool)
    print(f"   Success: {result}")
    
    print("\n🗑️ DELETE tool:")
    result = sync.delete_tool("github-issue-creator")
    print(f"   Success: {result}")
    
    print("\n📊 Sync Status:")
    status = sync.get_sync_status()
    print(json.dumps(status, indent=2))
    
    sync.close()


def run_daemon(poll_interval: int = 60):
    """Run the MCP sync daemon continuously."""
    sync = MCPSyncManager(poll_interval=poll_interval)
    import signal
    running = True
    def handler(signum, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    # Startup self-check: fail fast with clear diagnostics if runtime is misconfigured.
    try:
        sync.conn.execute("SELECT 1").fetchone()
    except Exception as e:
        print(f"[MCP Sync Daemon] Startup failed: DB unavailable at {DB_PATH} ({e})", flush=True)
        sync.close()
        return
    print(f"[MCP Sync Daemon] Starting (poll_interval={poll_interval}s, PID={os.getpid()})", flush=True)
    sync.start_background_sync()
    while running:
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            break
    sync.stop_background_sync()
    sync.close()
    print("[MCP Sync Daemon] Stopped", flush=True)

if __name__ == "__main__":
    import sys
    if "--daemon" in sys.argv:
        poll = int(sys.argv[sys.argv.index("--daemon") + 1]) if "--daemon" in sys.argv and len(sys.argv) > sys.argv.index("--daemon") + 1 else 60
        run_daemon(poll)
    else:
        demo()
