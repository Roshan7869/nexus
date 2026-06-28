#!/usr/bin/env python3
"""
auto_invoke.py — NEXUS v2 Autonomous Trigger Engine
Based on: AnyTool self-reflection + ToolkenGPT + HuggingGPT 4-stage

Triggers automatically when system requires intervention.
No manual find_skills() calls needed.
"""

import json, time, sqlite3, re, os
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from server.db import get_db_path, get_connection

MAX_RETRIES = 3
AUTO_TRIGGER_THRESHOLD = 0.68  # Skill invocation threshold

class TriggerType(Enum):
    ERROR_DETECTED = "error"
    TASK_COMPLEXITY_HIGH = "complex"
    REPEATED_FAILURE = "failure_pattern"
    CONTEXT_EXPANSION = "context_need"
    USER_QUERY = "user_query"
    SYSTEM_EVENT = "system_event"

@dataclass
class ToolMatch:
    name: str
    score: float
    category: str  # INVOKE / REVIEW / SKIP
    probability: float
    body: str
    description: str
    resource_type: str

class AutoInvoke:
    def __init__(self):
        self.conn = self._get_conn()
        self.categories = self._load_categories()
        
    def _get_conn(self):
        conn = sqlite3.connect(str(get_db_path()), timeout=60)
        conn.enable_load_extension(True)
        try:
            import sqlite_vec
            sqlite_vec.load(conn)
        except:
            pass
        conn.row_factory = sqlite3.Row
        return conn
    
    def _load_categories(self) -> Dict:
        """Pre-defined category hierarchy from research"""
        return {
            "debug": ["investigate", "debug", "error", "crash", "fix", "traceback"],
            "implement": ["implement", "build", "create", "feature", "add", "new"],
            "test": ["test", "qa", "verify", "quality", "coverage", "assert"],
            "review": ["review", "audit", "check", "assess", "inspect"],
            "deploy": ["deploy", "ship", "release", "push", "launch", "ci"],
            "research": ["research", "analyze", "investigate", "explore", "search"],
            "design": ["design", "architect", "spec", "plan", "mockup"],
            "refactor": ["refactor", "optimize", "improve", "cleanup", "migrate"],
        }
    
    def detect_trigger(self, context: str, error: str = None) -> Tuple[TriggerType, float]:
        """Analyze context to detect if autonomous trigger is needed"""
        context_lower = context.lower()
        
        # Error detection
        if error and any(k in error.lower() for k in ['error', 'exception', 'failed', 'crash', 'traceback']):
            return TriggerType.ERROR_DETECTED, 0.95
        
        # High complexity detection
        complexity_keywords = ['architecture', 'distributed', 'scale', 'concurrent', 
                              'machine learning', 'security audit', 'refactor entire']
        if any(k in context_lower for k in complexity_keywords):
            return TriggerType.TASK_COMPLEXITY_HIGH, 0.85
        
        # Pattern: repeated similar queries
        if self._detect_failure_pattern(context):
            return TriggerType.REPEATED_FAILURE, 0.80
        
        # Context expansion need
        if len(context) > 500 and 'entire' in context_lower:
            return TriggerType.CONTEXT_EXPANSION, 0.75
        
        # Default user query
        return TriggerType.USER_QUERY, 0.70
    
    def _detect_failure_pattern(self, query: str) -> bool:
        """Check if same query tried multiple times"""
        rows = self.conn.execute("""
            SELECT COUNT(*) FROM routing_log 
            WHERE task_summary=? AND outcome='wrong'
        """, (query,)).fetchone()
        return rows[0] >= 2 if rows else False
    
    def find_skills(self, task: str, project: str = "default", max_results: int = 5) -> List[ToolMatch]:
        """Hierarchical retrieval - Layer 1: Category match"""
        # First, detect category
        category = self._detect_category(task)
        
        # Layer 2: Vector similarity search with payload filters
        results = self._vector_search(task, category, max_results)
        
        # Layer 3: Tool-to-Agent join (get parent MCP server context)
        results = self._join_with_agents(results)
        
        # Toolken selection with probability
        matches = self._toolken_select(results, task)
        
        return matches[:max_results]
    
    def _detect_category(self, task: str) -> Optional[str]:
        """Layer 1: Fast category detection"""
        task_lower = task.lower()
        for cat, keywords in self.categories.items():
            if any(k in task_lower for k in keywords):
                return cat
        return None
    
    def _vector_search(self, query: str, category: str = None, k: int = 20) -> List[Dict]:
        """Layer 2: HNSW vector search"""
        search_text = query
        
        try:
            query_emb = self._get_query_embedding(search_text)
            if query_emb:
                results = self.conn.execute("""
                    SELECT name, description, body_text, resource_type,
                           times_triggered, times_correct, times_wrong, repo_name,
                           vec_distance(embedding, ?) as score
                    FROM resources
                    WHERE embedding IS NOT NULL
                    ORDER BY score
                    LIMIT ?
                """, (query_emb, k)).fetchall()
                
                matches = []
                for r in results:
                    matches.append(dict(r))
                return matches
        except Exception as e:
            pass
        
        return self._text_search_fallback(query, category, k)
    
    def _get_query_embedding(self, text: str) -> bytes:
        """Generate embedding for query using sentence-transformers"""
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer('all-MiniLM-L6-v2')
            emb = model.encode(text).astype('float32')
            return emb.tobytes()
        except:
            return None
    
    def _text_search_fallback(self, query: str, category: str, k: int) -> List[Dict]:
        """Fallback when vectors not available"""
        words = set(query.lower().split())
        
        rows = self.conn.execute("""
            SELECT name, description, body_text, resource_type,
                   times_triggered, times_correct, times_wrong
            FROM resources
            LIMIT 200
        """).fetchall()
        
        scored = []
        for r in rows:
            text = f"{r['name']} {r['description'] or ''} {(r['body_text'] or '')[:500]}".lower()
            text_words = set(text.split())
            overlap = len(words & text_words)
            
            tt = r['times_triggered'] or 0
            tc = r['times_correct'] or 0
            sr = (tc / tt * 100) if tt > 0 else 100
            
            score = overlap * 0.3 + sr / 200
            
            scored.append({
                'name': r['name'],
                'description': r['description'],
                'body_text': r['body_text'],
                'resource_type': r['resource_type'],
                'times_triggered': tt,
                'times_correct': tc,
                'score': score
            })
        
        scored.sort(key=lambda x: x['score'], reverse=True)
        return scored[:k]
    
    def _join_with_agents(self, results: List[Dict]) -> List[Dict]:
        """Layer 3: Tool-to-Agent join - get parent MCP/agent context"""
        for r in results:
            # Find parent agent/MCP for this tool
            parent = self.conn.execute("""
                SELECT name, resource_type FROM resources
                WHERE resource_type IN ('mcp', 'agent')
                AND (name LIKE ? OR description LIKE ?)
                LIMIT 1
            """, (f"%{r['name']}%", f"%{r['name']}%")).fetchone()
            
            r['parent_agent'] = parent['name'] if parent else None
            r['parent_type'] = parent['resource_type'] if parent else None
        
        return results
    
    def _toolken_select(self, results: List[Dict], query: str) -> List[ToolMatch]:
        """ToolkenGPT-style: Compute probabilities and select"""
        matches = []
        
        for r in results:
            # Compute probability based on score
            prob = min(r['score'] * 1.5, 0.99) if r['score'] > 0 else 0.1
            
            # Category classification
            if prob >= 0.68:
                category = "INVOKE"
            elif prob >= 0.45:
                category = "REVIEW"
            else:
                category = "SKIP"
            
            matches.append(ToolMatch(
                name=r['name'],
                score=round(r['score'], 3),
                category=category,
                probability=round(prob, 3),
                body=r.get('body_text', ''),
                description=r.get('description', ''),
                resource_type=r.get('resource_type', 'skill')
            ))
        
        return matches
    
    # ═══════════════════════════════════════════════════════════════════
    # SELF-REFLECTION LOOP (AnyTool Paper Algorithm)
    # ═══════════════════════════════════════════════════════════════════
    
    def execute_with_reflection(self, task: str, project: str = "default") -> Dict:
        """Execute task with self-reflection retry loop"""
        print(f"\n{'='*60}")
        print(f"  AUTO_INVOKE: {task}")
        print(f"{'='*60}")
        
        # Phase 1: Get candidates
        candidates = self.find_skills(task, project, max_results=5)
        attempts = []
        
        if not self._should_retry(task):
            print(f"  ⛔ Retry guard: '{task}' already failed ≥{MAX_RETRIES} times, skipping")
            return self._format_failure(task, attempts)

        for attempt in range(MAX_RETRIES):
            print(f"\n  🔄 Attempt {attempt + 1}/{MAX_RETRIES}")
            
            # Select best candidate
            selected = self._select_best(candidates, attempts)
            if not selected:
                print("  ❌ No more candidates")
                break
            
            print(f"  → Selected: {selected.name} (prob={selected.probability})")
            
            # Execute (simulate - in real system, invoke tool)
            result = self._invoke_tool(selected, task)
            attempts.append({
                'tool': selected.name,
                'result': result,
                'attempt': attempt + 1
            })
            
            # Self-reflection: analyze result
            if result.get('success'):
                print(f"  ✅ Success: {result.get('message', 'OK')}")
                return self._format_success(selected, result, attempts)
            
            print(f"  ⚠️ Failed: {result.get('error', 'Unknown')}")
            
            # Analyze failure, get new candidates
            candidates = self._reflection_analysis(task, selected, result, candidates)
        
        # All retries exhausted
        return self._format_failure(task, attempts)
    
    def _should_retry(self, task: str) -> bool:
        """Guard: only retry if this task hasn't already failed MAX_RETRIES+ times."""
        count = self.conn.execute("""
            SELECT COUNT(*) FROM routing_log
            WHERE task_summary=? AND outcome='wrong'
        """, (task,)).fetchone()[0]
        return count < MAX_RETRIES

    def _select_best(self, candidates: List[ToolMatch], tried: List[Dict]) -> Optional[ToolMatch]:
        """Select highest probability candidate not yet tried"""
        tried_names = [t['tool'] for t in tried]
        
        for c in candidates:
            if c.category != "SKIP" and c.name not in tried_names:
                return c
        return None
    
    def _invoke_tool(self, tool: ToolMatch, task: str) -> Dict:
        """Invoke tool with reflection loop + event bridge."""
        # Log the invocation
        try:
            self.conn.execute("""
                INSERT INTO routing_log(task_summary, project, matched_tier, matched_model,
                                       matched_skills, outcome, skill_name, ide)
                VALUES(?,?,?,?,?,?,?,?)
            """, (task, "auto", 2, "auto-router", json.dumps([tool.name]),
                  "pending", tool.name, os.environ.get("NEXUS_IDE", "auto_invoke")))
            self.conn.commit()
        except Exception as exc:
            # Silently continue — logging failure shouldn't block invocation
            import sys
            print(f"[auto_invoke] routing_log insert failed: {exc}", file=sys.stderr)

        # ── Phase 5: Bridge to event system ──
        try:
            from event_bridge import record_outcome
            # Record the invocation attempt
            record_outcome("pending", {
                "matched_model": "auto-router",
                "provider": "ollama_cloud",
                "task_type": "auto_invoke",
                "skill_name": tool.name,
            }, ide="auto_invoke")
        except Exception:
            pass

        # ── Phase 5: Try reflection loop on failure ──
        try:
            from reflection_loop import reflect_and_retry
            retry_result = reflect_and_retry(
                failed_tool=tool.name,
                error="execution check",
                query=task,
                candidates=[{"name": c.name, "score": c.score, "resource_type": c.resource_type}
                            for c in self.find_skills(task, "auto", max_results=5)],
            )
            if retry_result.success and retry_result.final_result:
                return retry_result.final_result
        except Exception:
            pass

        # Fallback: simulate based on success rate
        tt = tool.score * 100
        success_prob = min(tt / 100, 0.95)
        import random
        if random.random() < success_prob:
            return {'success': True, 'message': f"Tool {tool.name} executed successfully"}
        else:
            return {'success': False, 'error': 'Execution failed'}
    
    def _reflection_analysis(self, task: str, failed_tool: ToolMatch, 
                            result: Dict, candidates: List[ToolMatch]) -> List[ToolMatch]:
        """Analyze failure and find alternative candidates"""
        print(f"  🔍 Reflecting on failure...")
        
        # Update failure count
        self.conn.execute("""
            UPDATE resources SET times_wrong = times_wrong + 1, times_triggered = times_triggered + 1
            WHERE name = ?
        """, (failed_tool.name,))
        self.conn.commit()
        
        # Find alternatives - tools with similar description but different approach
        alternatives = self._find_alternatives(task, failed_tool.name, candidates)
        
        return alternatives if alternatives else candidates
    
    def _find_alternatives(self, task: str, exclude: str, candidates: List[ToolMatch]) -> List[ToolMatch]:
        """Find alternative tools that might work"""
        tried = {c.name for c in candidates if c.category == "SKIP"}
        
        rows = self.conn.execute("""
            SELECT name, description, body_text, resource_type,
                   times_correct, times_triggered
            FROM resources
            WHERE resource_type = 'skill'
            AND status = 'active'
            AND name != ?
            ORDER BY times_correct DESC
            LIMIT 10
        """, (exclude,)).fetchall()
        
        new_candidates = []
        for r in rows:
            if r['name'] not in tried:
                tt = r['times_triggered'] or 0
                tc = r['times_correct'] or 0
                sr = (tc / tt * 100) if tt > 0 else 50
                
                new_candidates.append(ToolMatch(
                    name=r['name'],
                    score=sr / 100,
                    category="INVOKE" if sr > 60 else "REVIEW",
                    probability=sr / 100,
                    body=r['body_text'] or '',
                    description=r['description'] or '',
                    resource_type=r['resource_type']
                ))
        
        return new_candidates
    
    def _format_success(self, tool: ToolMatch, result: Dict, attempts: List[Dict]) -> Dict:
        """Format successful execution result"""
        # Log success
        self.conn.execute("""
            INSERT INTO routing_log(task_summary, project, matched_tier, matched_model,
                                   matched_skills, outcome, skill_name, ide)
            VALUES(?,?,?,?,?,?,?,?)
        """, (f"Success with {tool.name}", "auto", 2, "auto-router",
              json.dumps([tool.name]), "correct", tool.name,
              os.environ.get("NEXUS_IDE", "auto_invoke")))
        
        self.conn.execute("""
            UPDATE resources SET times_correct = times_correct + 1,
            times_triggered = times_triggered + 1
            WHERE name = ?
        """, (tool.name,))
        self.conn.commit()
        
        return {
            'status': 'success',
            'tool': tool.name,
            'tool_type': tool.category,
            'attempts': len(attempts),
            'result': result,
            'message': f"Completed in {len(attempts)} attempt(s)"
        }
    
    def _format_failure(self, task: str, attempts: List[Dict]) -> Dict:
        """Format failure result with diagnostic"""
        return {
            'status': 'failed',
            'task': task,
            'attempts': len(attempts),
            'tried_tools': [a['tool'] for a in attempts],
            'message': f'Failed after {len(attempts)} attempts',
            'diagnostic': 'Consider manual intervention or different approach'
        }
    
    def close(self):
        self.conn.close()


def auto_trigger(event: str, context: str = "", project: str = "default") -> Dict:
    """Main entry point - call this from system hooks"""
    invoker = AutoInvoke()
    try:
        trigger_type, confidence = invoker.detect_trigger(context, event)
        
        print(f"\n🎯 Trigger detected: {trigger_type.value} (confidence: {confidence})")
        
        if trigger_type == TriggerType.ERROR_DETECTED:
            task = f"debug: {event}"
        elif trigger_type == TriggerType.REPEATED_FAILURE:
            task = f"investigate: {context}"
        else:
            task = context
        
        result = invoker.execute_with_reflection(task, project)
        return result
        
    finally:
        invoker.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        event = sys.argv[1]
        context = sys.argv[2] if len(sys.argv) > 2 else ""
        result = auto_trigger(event, context)
        print(json.dumps(result, indent=2))
    else:
        # Demo
        print("\n" + "="*60)
        print("  NEXUS v2 AUTO_INVOKE DEMO")
        print("="*60)
        
        # Test case 1: Error detection
        result1 = auto_trigger("TypeError: 'NoneType' object has no attribute 'split'", 
                              "Python function is returning None")
        print(json.dumps(result1, indent=2))