# NEXUS Second Brain: Multi-Level Hierarchical Execution Plan v2

**Author:** minimax-m3:cloud (NEXUS v4 architecture)
**Date:** 2026-06-26
**Research basis:** 15 arXiv papers, 20 GitHub repos, SSRN RL literature, NEXUS source audit (50+ Python files)

---

## Executive Summary

NEXUS currently operates as a 2,815-resource routing engine with v4 cluster routing, Thompson Sampling, and EFC computation (unwired). This plan transforms it into an autonomous "second brain" that:

1. Calls tools autonomously (no manual find_skills needed)
2. Integrates with multiple TUI IDEs simultaneously (Claude Code, OpenCode, Codex, Hermes, Aider)
3. Tracks harness efficiency per agent session
4. Builds a dense knowledge graph from tool I/O schemas
5. Wires EFC into ranking scores
6. Solves the cold-start problem (66.6% never triggered)
7. Fixes duplicate MCP process spawning
8. Unifies v3/v4 routing into a single state machine

---

## ARCHITECTURE: 5-Layer Stack

```
┌─────────────────────────────────────────────────────┐
│ Layer 5: TUI IDE Adapters                            │
│   Claude Code │ OpenCode │ Codex │ Hermes │ Aider   │
│   (tmux-based session orchestration)                │
├─────────────────────────────────────────────────────┤
│ Layer 4: Autonomous Tool Calling Pipeline            │
│   LangGraph State Machine + Hallucination Gate       │
│   before_model → LLM → after_model → reflexion       │
├─────────────────────────────────────────────────────┤
│ Layer 3: Unified Routing Engine                       │
│   Cluster Router → FAISS → ISO Score → EFC Blend     │
│   (replaces v3/v4 dichotomy)                          │
├─────────────────────────────────────────────────────┤
│ Layer 2: Learning & Feedback Loop                     │
│   Thompson Sampling + EFC + Reflexion + Auto-Close   │
│   Quality-weighted feedback score (arXiv 2605.29682)  │
├─────────────────────────────────────────────────────┤
│ Layer 1: Process Supervision & Knowledge Graph        │
│   supervisord + Graphify + SQLite + FAISS            │
└─────────────────────────────────────────────────────┘
```

---

## PHASE 1: Process Supervision Fix (Issue #1)

**Goal:** Eliminate duplicate MCP process spawning. Proper child reaping.

**Problem:** NEXUS spawns `nexus-mcp-enhanced.py` via subprocess without supervision. Crashes leave zombie processes. Gateway doesn't reap children. Multiple instances spawn.

**Solution:** Adopt supervisord (9K stars, battle-tested, Python-native).

### Steps

1. Install supervisord:
   ```bash
   pip install supervisor
   ```

2. Create `/home/roshan/nexus/supervisord.conf`:
   ```ini
   [supervisord]
   logfile=/home/roshan/nexus/logs/supervisord.log
   pidfile=/home/roshan/nexus/run/supervisord.pid
   nodaemon=false
   
   [program:nexus-http-daemon]
   command=python3 /home/roshan/nexus/server/nexus_http_daemon.py
   directory=/home/roshan/nexus
   autostart=true
   autorestart=true
   startsecs=3
   stopwaitsecs=10
   killasgroup=true
   stopasgroup=true
   stdout_logfile=/home/roshan/nexus/logs/http-daemon.log
   stderr_logfile=/home/roshan/nexus/logs/http-daemon.err
   priority=10
   
   [program:nexus-mcp-server]
   command=python3 /home/roshan/nexus/server/nexus-mcp-enhanced.py
   directory=/home/roshan/nexus
   numprocs=1
   autostart=true
   autorestart=true
   startsecs=3
   stopasgroup=true
   killasgroup=true
   stdout_logfile=/home/roshan/nexus/logs/mcp-server.log
   stderr_logfile=/home/roshan/nexus/logs/mcp-server.err
   priority=20
   ```

3. Add XML-RPC control to NEXUS routing layer:
   ```python
   import xmlrpc.client
   supervisor = xmlrpc.client.ServerProxy("http://127.0.0.1:9001/RPC2")
   
   def restart_mcp_server():
       supervisor.supervisor.stopProcess("nexus-mcp-server")
       supervisor.supervisor.startProcess("nexus-mcp-server")
   
   def get_process_status():
       info = supervisor.supervisor.getProcessInfo("nexus-mcp-server")
       return {"running": info["state"] == 20, "pid": info["pid"], "uptime": info["uptime"]}
   ```

4. Kill all existing stale processes, start under supervisord:
   ```bash
   pkill -f "nexus-mcp-enhanced.py"
   pkill -f "nexus_http_daemon"
   supervisord -c /home/roshan/nexus/supervisord.conf
   ```

**Verification:** `supervisorctl status` shows exactly 1 instance of each process. No zombies after crash.

**Rollback:** Stop supervisord, revert to direct python3 invocation.

**Estimated time:** 2 hours

---

## PHASE 2: Unified Routing Engine (Issue #4)

**Goal:** Replace v3 (hierarchy_router) vs v4 (tool_attention_v4) dichotomy with a single state-machine-based routing engine.

**Problem:** v3 and v4 disagree on top-1 picks 5/6 queries. v3 uses category keywords + HNSW. v4 uses cluster centroids + ISO embeddings. Different algorithms → different results → inconsistent user experience.

**Solution:** LangGraph state machine (36K stars). Each routing step is a graph node. One pipeline, no versions.

### Architecture

```python
# /home/roshan/nexus/server/unified_router.py

from typing import TypedDict, List, Tuple
from dataclasses import dataclass

@dataclass
class RouteResult:
    resource_id: int
    name: str
    score: float
    cluster: str
    method: str  # how it was found

class UnifiedRouter:
    """Single routing pipeline replacing v3/v4.
    
    Pipeline: query → cluster_router → faiss_search → iso_score → efc_blend → rank
    """
    
    def __init__(self):
        from server.cluster_router import ClusterRouter
        from server.cluster_faiss import ClusterNumpySearch
        from server.efc import EFCCalculator
        
        self.cluster_router = ClusterRouter()
        self.faiss = ClusterNumpySearch(str(DB_PATH))
        self.efc = EFCCalculator()
    
    def route(self, query: str, top_k: int = 10) -> List[RouteResult]:
        # Step 1: Cluster activation (v4 cluster routing)
        clusters = self.cluster_router.route_to_clusters(query, max_clusters=2)
        
        # Step 2: FAISS search within activated clusters
        candidates = self.faiss.search(query, clusters, top_k=top_k * 3)
        
        # Step 3: ISO scoring (v4 tool attention)
        scored = self._iso_score(candidates, query)
        
        # Step 4: EFC blend (new — wires EFC into ranking)
        blended = self._efc_blend(scored)
        
        # Step 5: Rank and return top_k
        ranked = sorted(blended, key=lambda x: x.score, reverse=True)[:top_k]
        return ranked
    
    def _iso_score(self, candidates, query):
        """ISO score from arXiv 2604.21816."""
        # θ-threshold gating: candidates with ISO < 0.28 are filtered
        theta = 0.28
        results = []
        for c in candidates:
            iso = c.get('cosine_similarity', 0)  # simplified
            if iso >= theta:
                results.append(RouteResult(
                    resource_id=c['id'],
                    name=c['name'],
                    score=iso,
                    cluster=c.get('cluster', 'general'),
                    method='iso'
                ))
        return results
    
    def _efc_blend(self, scored):
        """Blend ISO score with EFC and Thompson posterior.
        
        Formula: combined = 0.5 * iso + 0.2 * keyword_match + 0.3 * efc_normalized
        Where efc_normalized = EFC / max_EFC_observed
        """
        max_efc = self.efc.get_max_efc() or 1.0
        
        for r in scored:
            efc_val = self.efc.get_efc(r.resource_id)
            efc_norm = efc_val / max_efc if max_efc > 0 else 0
            
            # Thompson posterior (Beta distribution mean)
            alpha = self._get_times_correct(r.resource_id) + 1
            beta = self._get_times_wrong(r.resource_id) + 1
            thompson_mean = alpha / (alpha + beta)
            
            # Final blend: 0.5*ISO + 0.2*Thompson + 0.3*EFC
            r.score = 0.5 * r.score + 0.2 * thompson_mean + 0.3 * efc_norm
        
        return scored
```

### Steps

1. Write `unified_router.py` (above)
2. Update all MCP handlers to use `UnifiedRouter` instead of v3/v4:
   ```python
   # In nexus-mcp-enhanced.py
   from server.unified_router import UnifiedRouter
   router = UnifiedRouter()
   
   def handle_find_skills(task):
       return router.route(task)
   ```
3. Deprecate `hierarchy_router.py` and `tool_attention_v4.py` (keep as fallback)
4. Update HTTP daemon endpoints to use unified router
5. Run A/B test: unified vs v3 and v4 on 20 known queries

**Verification:** Top-1 agreement >95% vs both v3 and v4 on test suite.

**Estimated time:** 4 hours

---

## PHASE 3: EFC Integration (Issue #7)

**Goal:** Wire EFC (Effective Feedback Compute) into the combined_score formula.

**Problem:** EFC values are computed (EFC=4.500, NRS-EFC=3.033 appear in `notes` column) but dedicated columns remain NULL. The UPDATE statement in `nexus_http_daemon.py` fails silently.

### Root Cause Analysis

The EFC bridge in `nexus_http_daemon.py` tries to UPDATE 7 dedicated columns but the column names in the SQL don't match the schema migration. Let me diagnose and fix.

### Steps

1. Diagnose the EFC UPDATE failure:
   ```sql
   -- Check if columns exist
   PRAGMA table_info(outcome_log);
   -- Check for any non-NULL EFC values
   SELECT COUNT(*) FROM outcome_log WHERE efc_score IS NOT NULL;
   -- Check what's in notes
   SELECT id, notes, efc_score FROM outcome_log WHERE notes LIKE '%EFC%' LIMIT 5;
   ```

2. Fix the EFC bridge in `nexus_http_daemon.py`:
   ```python
   # The issue is likely column name mismatch or type error
   # Ensure the UPDATE uses the correct column names from the migration
   
   def _bridge_efc(self, conn, outcome_id, efc_data):
       """Write EFC values to dedicated columns."""
       try:
           conn.execute("""
               UPDATE outcome_log SET
                   efc_score = ?,
                   efc_normalized = ?,
                   nrs_efc = ?,
                   difficulty_estimate = ?,
                   harness_efficiency = ?,
                   repeat_penalty = ?,
                   first_use_bonus = ?
               WHERE id = ?
           """, (
               efc_data.get('efc_score'),
               efc_data.get('efc_normalized'),
               efc_data.get('nrs_efc'),
               efc_data.get('difficulty_estimate'),
               efc_data.get('harness_efficiency'),
               efc_data.get('repeat_penalty'),
               efc_data.get('first_use_bonus'),
               outcome_id
           ))
           conn.commit()
       except Exception as e:
           logger.error(f"EFC bridge UPDATE failed: {e}")
           # Log the actual column names for debugging
           cols = conn.execute("PRAGMA table_info(outcome_log)").fetchall()
           logger.error(f"Available columns: {[c[1] for c in cols]}")
   ```

3. Backfill existing outcomes:
   ```python
   # /home/roshan/nexus/scripts/backfill_efc.py
   import sqlite3
   from server.efc import EFCCalculator
   
   conn = sqlite3.connect("/home/roshan/nexus/db/nexus.db")
   efc = EFCCalculator()
   
   rows = conn.execute("SELECT id, skill_name, outcome, created_at FROM outcome_log").fetchall()
   for row in rows:
       efc_vals = efc.compute(row[1], row[2], row[3])
       conn.execute("""
           UPDATE outcome_log SET
               efc_score=?, efc_normalized=?, nrs_efc=?,
               difficulty_estimate=?, harness_efficiency=?,
               repeat_penalty=?, first_use_bonus=?
           WHERE id=?
       """, (efc_vals['efc_score'], efc_vals['efc_normalized'], 
             efc_vals['nrs_efc'], efc_vals['difficulty_estimate'],
             efc_vals['harness_efficiency'], efc_vals['repeat_penalty'],
             efc_vals['first_use_bonus'], row[0]))
   conn.commit()
   ```

4. Verify EFC is now populating:
   ```sql
   SELECT COUNT(*) FROM outcome_log WHERE efc_score IS NOT NULL;
   -- Should be > 0
   ```

**Verification:** EFC columns populated for >90% of outcome_log rows.

**Estimated time:** 2 hours

---

## PHASE 4: Autonomous Tool Calling (Issue #2, #11)

**Goal:** Model autonomously selects and calls tools without manual MCP routing.

**Problem:** Currently, NEXUS requires manual `find_skills()` or `route_v4()` calls. The model can't call tools autonomously — the IDE must invoke NEXUS first.

**Solution:** LangGraph middleware pattern (Paper 15) + Tool Attention before/after_model hooks (Paper 4).

### Architecture

```
User Query → [before_model: NEXUS injects tool set] → [LLM generates response + tool calls] → [after_model: NEXUS validates/gates] → [Tool execution] → [reflexion: outcome logged]
```

### Implementation

```python
# /home/roshan/nexus/server/autonomous_pipeline.py

class AutonomousToolCaller:
    """LangGraph-style middleware for autonomous tool calling.
    
    Based on: arXiv 2604.21816 (Tool Attention) + LangGraph middleware pattern.
    """
    
    def __init__(self):
        from server.unified_router import UnifiedRouter
        from server.hallucination import HallucinationGate
        
        self.router = UnifiedRouter()
        self.gate = HallucinationGate()
    
    def before_model(self, query: str, agent_state: dict) -> dict:
        """Inject relevant tools into the model's context.
        
        Called BEFORE the LLM generates a response.
        Returns the active tool set + schemas for the model to use.
        """
        # Route query to find relevant tools
        results = self.router.route(query, top_k=10)
        
        # Build tool summaries for context injection
        tool_summaries = []
        active_tool_ids = []
        schemas = {}
        
        for r in results:
            tool_summaries.append(f"- {r.name}: {r.method} (score={r.score:.3f})")
            active_tool_ids.append(r.resource_id)
            # Load schema from DB
            schema = self._load_schema(r.resource_id)
            if schema:
                schemas[r.name] = schema
        
        tools_text = "\n".join(tool_summaries)
        
        return {
            "active_tool_ids": active_tool_ids,
            "tool_schemas": schemas,
            "tools_text": f"Available tools:\n{tools_text}",
            "agent_state": agent_state
        }
    
    def after_model(self, response: str, active_tool_ids: list) -> dict:
        """Validate model's tool calls against the active set.
        
        Called AFTER the LLM generates a response.
        Rejects hallucinated tool calls not in the active set.
        """
        # Extract tool calls from model response
        tool_calls = self._extract_tool_calls(response)
        
        rejections = []
        validated = []
        
        for tc in tool_calls:
            if tc.tool_id in active_tool_ids:
                validated.append(tc)
            else:
                # Hallucination gate: reject tools not in active set
                rejection = self.gate.reject(tc.tool_id, active_tool_ids)
                rejections.append(rejection)
        
        return {
            "validated_calls": validated,
            "rejected_calls": rejections,
            "hallucination_detected": len(rejections) > 0
        }
    
    def reflexion(self, tool_call, tool_result, query) -> dict:
        """Post-execution reflexion for implicit outcome logging.
        
        Based on: arXiv 2303.11366 (Reflexion).
        Generates a verbal reflection on whether the tool helped.
        Logs implicit outcome if user didn't provide explicit feedback.
        """
        reflection_prompt = f"""Task: {query}
Tool called: {tool_call.name}
Tool output: {str(tool_result)[:500]}

Reflect: Did this tool help complete the task?
Answer with JSON: {{"helpful": true/false, "reason": "..."}}
"""
        # Use lightweight model for reflexion
        reflection = self._generate_reflection(reflection_prompt)
        
        try:
            import json
            parsed = json.loads(reflection)
            outcome = 'correct' if parsed.get('helpful') else 'wrong'
            return {"outcome": outcome, "reflection": parsed.get('reason', '')}
        except:
            return {"outcome": "routed", "reflection": ""}
    
    def _extract_tool_calls(self, response):
        """Parse tool calls from model response (function calling format)."""
        import re, json
        calls = []
        # Match function call patterns
        pattern = r'<tool_call>\s*(\{.*?\})\s*