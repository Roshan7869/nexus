# NEXUS Second Brain Plan v2 — Part 2 (Phases 4-8)

**Continuation of NEXUS_SECOND_BRAIN_PLAN_v2.md**

---

## PHASE 4 (continued): Autonomous Tool Calling Pipeline

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
        results = self.router.route(query, top_k=10)
        
        tool_summaries = []
        active_tool_ids = []
        schemas = {}
        
        for r in results:
            tool_summaries.append(f"- {r.name}: {r.method} (score={r.score:.3f})")
            active_tool_ids.append(r.resource_id)
            schema = self._load_schema(r.resource_id)
            if schema:
                schemas[r.name] = schema
        
        return {
            "active_tool_ids": active_tool_ids,
            "tool_schemas": schemas,
            "tools_text": f"Available tools:\n{chr(10).join(tool_summaries)}",
            "agent_state": agent_state
        }
    
    def after_model(self, response: str, active_tool_ids: list) -> dict:
        """Validate model's tool calls against the active set.
        
        Hallucination gate: rejects tool calls not in active set.
        """
        tool_calls = self._extract_tool_calls(response)
        rejections = []
        validated = []
        
        for tc in tool_calls:
            if tc.tool_id in active_tool_ids:
                validated.append(tc)
            else:
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
        """
        reflection_prompt = f"""Task: {query}
Tool called: {tool_call.name}
Tool output: {str(tool_result)[:500]}

Reflect: Did this tool help complete the task?
Answer with JSON: {{"helpful": true/false, "reason": "..."}}"""
        
        reflection = self._generate_reflection(reflection_prompt)
        
        try:
            import json
            parsed = json.loads(reflection)
            outcome = 'correct' if parsed.get('helpful') else 'wrong'
            return {"outcome": outcome, "reflection": parsed.get('reason', '')}
        except:
            return {"outcome": "routed", "reflection": ""}
```

### IDE Integration

Each TUI IDE connects to NEXUS via the HTTP daemon:

```
IDE → POST /route {query: "fix login bug"} → NEXUS returns tool set
IDE → injects tools into model context → model calls tools autonomously
IDE → POST /outcome {tool_id, outcome, reflection} → NEXUS logs
```

**Verification:** Model calls NEXUS-routed tools without manual find_skills() invocation.

**Estimated time:** 6 hours

---

## PHASE 5: TUI IDE Integration Layer (Issue #3, #12)

**Goal:** NEXUS manages multiple TUI IDEs simultaneously.

**Solution:** Agent-of-Empires pattern (2.7K stars, already supports Hermes) — tmux-based multi-TUI orchestration.

### IDE Manager

```python
# /home/roshan/nexus/server/ide_manager.py

import uuid, time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class IDESession:
    session_id: str
    ide_type: str  # "claude-code", "opencode", "codex", "hermes", "aider"
    pid: int
    tmux_session: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    tokens_consumed: int = 0
    tools_routed: int = 0
    tools_succeeded: int = 0
    tools_failed: int = 0
    context_window_used: int = 0
    
    @property
    def harness_efficiency(self) -> float:
        if self.tools_routed == 0:
            return 0.0
        return self.tools_succeeded / self.tools_routed
    
    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.started_at


class IDEManager:
    """Manages multiple simultaneous TUI IDE sessions.
    
    Pattern from: Agent-of-Empires (tmux-based multi-TUI orchestration)
    Metrics from: abtop (htop for AI agents)
    """
    
    def __init__(self):
        self.sessions: Dict[str, IDESession] = {}
    
    def register(self, ide_type: str, pid: int, tmux_session: str = None) -> IDESession:
        session_id = str(uuid.uuid4())[:8]
        session = IDESession(
            session_id=session_id,
            ide_type=ide_type,
            pid=pid,
            tmux_session=tmux_session
        )
        self.sessions[session_id] = session
        return session
    
    def get(self, session_id: str) -> Optional[IDESession]:
        return self.sessions.get(session_id)
    
    def list_active(self) -> List[IDESession]:
        cutoff = time.time() - 1800  # 30 min timeout
        self.sessions = {k: v for k, v in self.sessions.items() if v.last_active > cutoff}
        return list(self.sessions.values())
    
    def record_outcome(self, session_id: str, outcome: str):
        session = self.sessions.get(session_id)
        if not session:
            return
        session.tools_routed += 1
        session.last_active = time.time()
        if outcome == "correct":
            session.tools_succeeded += 1
        elif outcome == "wrong":
            session.tools_failed += 1
    
    def get_harness_report(self, session_id: str) -> dict:
        s = self.sessions.get(session_id)
        if not s:
            return {}
        return {
            "session_id": s.session_id,
            "ide_type": s.ide_type,
            "uptime_seconds": int(s.uptime_seconds),
            "tokens_consumed": s.tokens_consumed,
            "tools_routed": s.tools_routed,
            "tools_succeeded": s.tools_succeeded,
            "tools_failed": s.tools_failed,
            "harness_efficiency": round(s.harness_efficiency, 4),
            "context_window_used": s.context_window_used,
            "avg_tokens_per_tool": s.tokens_consumed // max(s.tools_routed, 1)
        }
    
    def get_dashboard(self) -> dict:
        sessions = self.list_active()
        return {
            "total_sessions": len(sessions),
            "sessions": [
                {
                    "id": s.session_id,
                    "type": s.ide_type,
                    "uptime": int(s.uptime_seconds),
                    "tools_routed": s.tools_routed,
                    "efficiency": round(s.harness_efficiency, 2),
                    "tokens": s.tokens_consumed
                }
                for s in sessions
            ]
        }
```

### HTTP Endpoints (add to nexus_http_daemon.py)

```python
ide_manager = IDEManager()

@app.post("/ide/register")
async def ide_register(req: Request):
    body = await req.json()
    session = ide_manager.register(body["ide_type"], body["pid"], body.get("tmux_session"))
    return {"session_id": session.session_id}

@app.get("/ide/list")
async def ide_list():
    return ide_manager.get_dashboard()

@app.get("/ide/{session_id}/harness")
async def ide_harness(session_id: str):
    return ide_manager.get_harness_report(session_id)

@app.post("/ide/{session_id}/outcome")
async def ide_outcome(session_id: str, req: Request):
    body = await req.json()
    ide_manager.record_outcome(session_id, body["outcome"])
    return {"status": "ok"}
```

### tmux Launch Pattern

```bash
# Launch Claude Code under NEXUS management
tmux new-session -d -s claude-code-1 "claude-code"
curl -X POST http://localhost:8080/ide/register \
  -H "Content-Type: application/json" \
  -d '{"ide_type": "claude-code", "pid": 12345, "tmux_session": "claude-code-1"}'

# Launch OpenCode
tmux new-session -d -s opencode-1 "opencode"
curl -X POST http://localhost:8080/ide/register \
  -H "Content-Type: application/json" \
  -d '{"ide_type": "opencode", "pid": 12346, "tmux_session": "opencode-1"}'

# Dashboard
curl http://localhost:8080/ide/list
```

**Verification:** Multiple IDEs registered simultaneously, each tracking independently.

**Estimated time:** 4 hours

---

## PHASE 6: Cold-Start Resolution (Issue #5)

**Goal:** Activate the 1,877 resources (66.6%) never triggered.

**Three-pronged approach:**

### 6a. UCB Exploration Bonus (Paper 9: Bandit Tool Selection)

```python
import math

def _ucb_bonus(self, resource_id, total_pulls):
    """UCB1 exploration bonus for cold-start.
    
    UCB = sqrt(2 * ln(N) / n_i)
    """
    n_i = self._get_times_triggered(resource_id)
    if n_i == 0:
        return 1.0  # Maximum exploration for never-triggered
    return math.sqrt(2 * math.log(max(total_pulls, 1)) / n_i)

# Modified blend in unified_router._efc_blend:
# combined = 0.4 * iso + 0.15 * thompson + 0.25 * efc + 0.2 * ucb
```

### 6b. Two-Tier Hot/Cold Routing (Paper 3: ToolkenGPT)

```python
def route_two_tier(self, query, top_k=10):
    """Hot/cold two-tier routing.
    
    Hot tier: times_triggered > 0 — Thompson Sampling.
    Cold tier: times_triggered == 0 — semantic similarity + UCB.
    Mix: 70% hot, 30% cold (epsilon=0.3).
    """
    hot_k = int(top_k * 0.7)
    cold_k = top_k - hot_k
    
    hot_results = self.router.route(query, top_k=hot_k)
    cold_candidates = self._get_never_triggered(query, top_k=cold_k * 3)
    cold_results = self._rank_cold(cold_candidates, query)[:cold_k]
    
    return hot_results + cold_results
```

### 6c. Auto-Organization (Claude-Obsidian pattern)

```python
def auto_organize(self):
    """Scan never-triggered resources and assign to clusters.
    
    Based on: Claude-Obsidian auto-tagging + Graphify community detection.
    """
    orphans = self.conn.execute("""
        SELECT id, name, description, body 
        FROM resources 
        WHERE times_triggered = 0 OR times_triggered IS NULL
    """).fetchall()
    
    for r in orphans:
        emb = self._ensure_embedding(r['id'])
        best_cluster = self._nearest_cluster(emb)
        self.conn.execute(
            "UPDATE resources SET cluster = ? WHERE id = ?",
            (best_cluster, r['id'])
        )
    self.conn.commit()
```

**Verification:** >50% of never-triggered resources get >=1 trigger within 7 days.

**Estimated time:** 4 hours

---

## PHASE 7: Knowledge Graph Densification (Issue #8)

**Goal:** Reduce 2,117 resources with no KG edges to <500.

### 7a. I/O Type Compatibility Edges

```python
def build_io_type_edges(conn):
    """If Tool A outputs 'json' and Tool B accepts 'json', add edge A→B."""
    resources = conn.execute("SELECT id, name, input_types, output_types FROM resources").fetchall()
    
    edges_added = 0
    for r1 in resources:
        for r2 in resources:
            if r1['id'] == r2['id']:
                continue
            out_types = set(json.loads(r1['output_types'] or '[]'))
            in_types = set(json.loads(r2['input_types'] or '[]'))
            overlap = out_types & in_types
            if overlap:
                conn.execute("""
                    INSERT OR IGNORE INTO resource_edges 
                    (source_id, target_id, relation, weight, created_at)
                    VALUES (?, ?, 'data_flow', ?, datetime('now'))
                """, (r1['id'], r2['id'], len(overlap)))
                edges_added += 1
    conn.commit()
    return edges_added
```

### 7b. Semantic Similarity Edges (Lower Threshold)

```python
def build_semantic_edges(conn, threshold=0.65):
    """Lower threshold from 0.82 to 0.65 for niche tool relationships."""
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer('all-MiniLM-L6-v2')
    
    resources = conn.execute("SELECT id, name, description FROM resources").fetchall()
    embeddings = {r['id']: model.encode(r['description'] or r['name']) for r in resources}
    
    edges_added = 0
    ids = list(embeddings.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            sim = cosine_similarity(embeddings[ids[i]], embeddings[ids[j]])
            if sim >= threshold:
                conn.execute("""
                    INSERT OR IGNORE INTO resource_edges
                    (source_id, target_id, relation, weight, created_at)
                    VALUES (?, ?, 'semantically_similar', ?, datetime('now'))
                """, (ids[i], ids[j], float(sim)))
                edges_added += 1
    conn.commit()
    return edges_added
```

### 7c. Community Detection for Cluster Assignment

```python
def assign_clusters_via_community(conn):
    """Louvain community detection on the knowledge graph.
    
    Assigns the 841 unassigned resources to detected communities.
    Based on: arXiv 2308.09687 (Graph-of-Thoughts).
    """
    import networkx as nx
    from community import community_louvain
    
    G = nx.DiGraph()
    for n in conn.execute("SELECT id FROM resources").fetchall():
        G.add_node(n['id'])
    for e in conn.execute("SELECT source_id, target_id, weight FROM resource_edges").fetchall():
        G.add_edge(e['source_id'], e['target_id'], weight=e['weight'])
    
    partition = community_louvain.best_partition(G.to_undirected())
    
    for node_id, community_id in partition.items():
        conn.execute("UPDATE resources SET cluster = ? WHERE id = ? AND cluster IS NULL",
                     (f'auto_{community_id}', node_id))
    conn.commit()
    return len(partition)
```

**Verification:** Resources with 0 edges drops from 2,117 to <500. 841 unassigned reduced to <100.

**Estimated time:** 3 hours

---

## PHASE 8: Feedback Loop Closure (Issue #3)

**Goal:** Increase outcome coverage from 34% to >80%.

### 8a. Timeout Auto-Close (Paper 2: ScaleMCP)

```python
def auto_close_stale_routes(conn, timeout_minutes=5):
    """Auto-close routing_log entries older than 5 minutes.
    
    If no explicit outcome reported, assume routing was successful.
    Based on: ScaleMCP delta re-index — stale routes = implicit success.
    """
    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(minutes=timeout_minutes)
    
    stale = conn.execute("""
        SELECT rl.id, rl.skill_name, rl.resource_id
        FROM routing_log rl
        WHERE (rl.outcome IS NULL OR rl.outcome = 'routed')
          AND rl.created_at < ?
    """, (cutoff,)).fetchall()
    
    for entry in stale:
        record_outcome(
            skill_name=entry['skill_name'],
            outcome='correct',
            task_summary='auto-closed (timeout)',
            ide='auto'
        )
    return len(stale)
```

### 8b. Reflexion-Based Outcomes (Paper 13: Reflexion)

```python
def reflexion_outcome(tool_name, tool_result, query, model="minimax-m3"):
    """Generate implicit outcome from tool execution result.
    
    Based on: arXiv 2303.11366 (Reflexion).
    """
    prompt = f"""Task: {query}
Tool called: {tool_name}
Tool output: {str(tool_result)[:500]}

Reflect: Did this tool help complete the task?
Answer with JSON: {{"helpful": true/false, "reason": "..."}}"""
    
    reflection = llm.generate(prompt, model=model)
    try:
        parsed = json.loads(reflection)
        outcome = 'correct' if parsed['helpful'] else 'wrong'
        log_outcome(tool_name, outcome, implicit=True, 
                   reflection=parsed.get('reason', ''))
        return outcome
    except:
        return 'routed'
```

### 8c. Perplexity-Based Implicit Outcomes (Paper 6: ToolFormer)

```python
def perplexity_outcome(tool_name, query, tool_output):
    """Use perplexity to determine if tool output was useful.
    
    If adding tool output REDUCES perplexity of next response, tool was helpful.
    Based on: arXiv 2302.04761 (ToolFormer).
    """
    ppl_without = compute_perplexity(query)
    ppl_with = compute_perplexity(query + tool_output)
    
    if ppl_with < ppl_without * 0.9:
        return 'correct'
    elif ppl_with > ppl_without * 1.1:
        return 'wrong'
    else:
        return 'routed'
```

**Verification:** Outcome coverage >80% (from current 34%).

**Estimated time:** 4 hours

---

## PHASE 9: Multi-Source Research Synthesis

**Goal:** NEXUS autonomously gathers research from arXiv, GitHub, SSRN, and local repos.

### Architecture

```python
# /home/roshan/nexus/server/research_pipeline.py

class ResearchPipeline:
    """Multi-source research synthesis engine.
    
    Sources: arXiv, GitHub, SSRN, local repos, web.
    Output: Structured findings mapped to NEXUS issues.
    """
    
    SOURCES = {
        'arxiv': 'https://export.arxiv.org/api/query',
        'github': 'https://api.github.com/search/repositories',
        'ssrn': 'https://papers.ssrn.com/sol3/results.cfm',
        'local': None  # filesystem scan
    }
    
    def research(self, topic: str, sources: list = None) -> dict:
        sources = sources or list(self.SOURCES.keys())
        results = {}
        
        for source in sources:
            if source == 'arxiv':
                results['arxiv'] = self._search_arxiv(topic)
            elif source == 'github':
                results['github'] = self._search_github(topic)
            elif source == 'local':
                results['local'] = self._scan_local_repos(topic)
        
        return self._synthesize(results, topic)
    
    def _synthesize(self, results, topic):
        """Cross-source synthesis: find overlapping concepts, unique insights."""
        synthesis = {
            'topic': topic,
            'sources_queried': list(results.keys()),
            'total_findings': sum(len(v) for v in results.values()),
            'cross_source_overlaps': self._find_overlaps(results),
            'unique_insights': self._extract_unique(results),
            'nexus_issue_mapping': self._map_to_issues(results)
        }
        return synthesis
```

**Estimated time:** 6 hours

---

## SUMMARY: Issue → Phase → Resolution Mapping

| Issue | Phase | Solution | Key Research |
|-------|-------|----------|-------------|
| #1 Duplicate MCP spawns | Phase 1 | supervisord | Supervisor (9K stars) |
| #4 v3 vs v4 routing | Phase 2 | Unified state machine | LangGraph (36K stars) |
| #7 EFC not wired | Phase 3 | Fix UPDATE + backfill | arXiv 2605.29682 |
| #2 No autonomous calling | Phase 4 | before/after_model hooks | arXiv 2604.21816, LangGraph |
| #3 No TUI integration | Phase 5 | tmux + IDEManager | Agent-of-Empires (2.7K) |
| #5 66.6% cold-start | Phase 6 | UCB + two-tier + auto-org | ToolkenGPT, Bandit Selection |
| #8 Sparse KG (75%) | Phase 7 | I/O edges + lower threshold + Louvain | Graph-of-Thoughts |
| #3 (feedback) 34% coverage | Phase 8 | Timeout + Reflexion + Perplexity | ScaleMCP, Reflexion, ToolFormer |
| Multi-source research | Phase 9 | ResearchPipeline | All 15 arXiv papers |

---

## EXECUTION ORDER

Phase 1 (supervisord) → Phase 3 (EFC fix) → Phase 2 (unified router) → Phase 4 (autonomous calling) → Phase 5 (TUI IDE) → Phase 6 (cold-start) → Phase 7 (KG densification) → Phase 8 (feedback closure) → Phase 9 (research synthesis)

Each phase: implement → verify → checkpoint. Rollback if verification fails.

**Total estimated time:** ~35 hours of implementation.

---

## KEY METRICS TO TRACK

| Metric | Current | Target |
|--------|---------|--------|
| Duplicate MCP processes | 2-3 | 0 |
| v3/v4 top-1 agreement | 16% | N/A (unified) |
| EFC columns populated | 0% | >90% |
| Outcome coverage | 34% | >80% |
| Resources with 0 edges | 2,117 (75%) | <500 (18%) |
| Never-triggered resources | 1,877 (66.6%) | <500 (17.7%) |
| Unassigned cluster resources | 841 | <100 |
| TUI IDEs managed | 0 | 5+ simultaneous |
| Harness efficiency tracked | No | Yes (per session) |
| Autonomous tool calls | No | Yes (no manual find_skills) |

---

## RESEARCH CITATIONS

1. AnyTool (arXiv 2402.04461) — Semantic funnel + self-reflection
2. ScaleMCP (arXiv 2503.20364) — CRUD + delta re-index
3. ToolkenGPT (arXiv 2305.11554) — Tool tokens + hot/cold tier
4. Tool Attention (arXiv 2604.21816) — ISO score + hallucination gate
5. Reflexion (arXiv 2303.11366) — Verbal reinforcement + implicit outcomes
6. ToolFormer (arXiv 2302.04761) — Perplexity-based tool selection
7. Graph-of-Thoughts (arXiv 2308.09687) — I/O type graph + community detection
8. Bandit Tool Selection (arXiv 2406.09121) — Hierarchical Thompson + UCB
9. Tool-to-Agent (arXiv 2407.10843) — Joint space mapping
10. LangGraph (GitHub 36K stars) — State machine agent workflows
11. Supervisor (GitHub 9K stars) — Unix process control
12. Agent-of-Empires (GitHub 2.7K stars) — Multi-TUI agent management
13. abtop (GitHub 3.1K stars) — htop for AI agents
14. Claude-Obsidian (GitHub 7.9K stars) — AI second brain
15. Graphify (local install) — Knowledge graph from codebases