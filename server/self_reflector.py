#!/usr/bin/env python3
"""
self_reflector.py — NEXUS v2 Self-Reflection Loop
Based on: AnyTool self-reflection mechanism

When a tool fails, the system:
1. Analyzes the failure
2. Selects alternative tool from candidates
3. Retries with learned context
4. Max 3 attempts
"""
from server.db import get_db_path as _resolve_db_path

import json, sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

DB_PATH = _resolve_db_path()

@dataclass
class ReflectionResult:
    diagnosis: str
    should_retry: bool
    alternative_tools: List[str]
    learned_approach: str

class SelfReflector:
    """
    Self-reflection mechanism based on AnyTool paper.
    
    Key insight: Don't just fail - analyze why and find alternatives.
    """
    
    def __init__(self):
        self.conn = self._get_conn()
        self.max_retries = 3
        
        # Error patterns and their typical solutions
        self.error_patterns = {
            'null_none': ['investigate', 'debug', 'error-handling'],
            'type_error': ['investigate', 'debug', 'type-checking'],
            'import_error': ['research', 'search-first', 'setup'],
            'permission': ['investigate', 'security-review', 'permissions'],
            'timeout': ['investigate', 'performance', 'retry'],
            'not_found': ['research', 'search-first', 'investigate'],
            'syntax': ['debug', 'code-review', 'lint'],
            'memory': ['investigate', 'optimization', 'performance'],
            'network': ['investigate', 'deployment', 'monitoring'],
        }
        
    def _get_conn(self):
        conn = sqlite3.connect(str(DB_PATH), timeout=60)
        conn.enable_load_extension(True)
        conn.row_factory = sqlite3.Row
        return conn
    
    def reflect(self, failed_tool: str, error: str, query: str,
                candidates: List[Dict]) -> ReflectionResult:
        """
        Analyze failure and determine next steps.
        
        Returns:
            - diagnosis: What went wrong
            - should_retry: Whether to try alternative
            - alternative_tools: List of tools to try next
            - learned_approach: Strategy for next attempt
        """
        # Classify the error
        error_type = self._classify_error(error)
        
        # Get recommended tools for this error type
        recommended_cats = self.error_patterns.get(error_type, ['debug', 'investigate'])
        
        # Find alternative candidates
        alternatives = self._find_alternatives(
            failed_tool=failed_tool,
            query=query,
            recommended_categories=recommended_cats,
            candidates=candidates
        )
        
        # Generate diagnosis
        diagnosis = self._generate_diagnosis(failed_tool, error, error_type)
        
        # Determine if should retry
        should_retry = len(alternatives) > 0 and error_type in self.error_patterns
        
        # Generate learned approach
        learned = self._generate_learned_approach(error_type, failed_tool, alternatives)
        
        return ReflectionResult(
            diagnosis=diagnosis,
            should_retry=should_retry,
            alternative_tools=[a['name'] for a in alternatives],
            learned_approach=learned
        )
    
    def _classify_error(self, error: str) -> str:
        """Classify error into known patterns"""
        error_lower = error.lower()
        
        patterns = [
            ('null_none', ['none', 'null', 'undefined', 'object has no attribute']),
            ('type_error', ['typeerror', 'type error', 'expected', 'got ']),
            ('import_error', ['importerror', 'modulenotfound', 'no module']),
            ('permission', ['permission', 'denied', 'access denied', 'forbidden']),
            ('timeout', ['timeout', 'timed out', 'connection refused']),
            ('not_found', ['not found', 'does not exist', '404', 'no such']),
            ('syntax', ['syntax', 'indentation', 'unexpected token']),
            ('memory', ['memory', 'out of', 'overflow', 'stack']),
            ('network', ['network', 'connection', 'reset', 'refused']),
        ]
        
        for pattern_name, keywords in patterns:
            if any(kw in error_lower for kw in keywords):
                return pattern_name
        
        return 'unknown'
    
    def _find_alternatives(self, failed_tool: str, query: str,
                          recommended_categories: List[str],
                          candidates: List[Dict]) -> List[Dict]:
        rows = self.conn.execute("""
            SELECT name, description, body_text, resource_type,
                   times_correct, times_triggered
            FROM resources
            WHERE resource_type = 'skill'
            AND status = 'active'
            AND name != ?
            ORDER BY times_correct DESC
            LIMIT 5
        """, (failed_tool,)).fetchall()
        
        if len(rows) < 2:
            rows = self.conn.execute("""
                SELECT name, description, body_text, resource_type,
                       times_correct, times_triggered
                FROM resources
                WHERE status = 'active'
                AND name != ?
                ORDER BY times_correct DESC
                LIMIT 5
            """, (failed_tool,)).fetchall()
        
        alternatives = []
        tried_names = {c.get('name') for c in candidates if c.get('name')}
        
        for r in rows:
            if r['name'] not in tried_names:
                tt = r['times_triggered'] or 0
                tc = r['times_correct'] or 0
                sr = (tc / tt * 100) if tt > 0 else 50
                
                alternatives.append({
                    'name': r['name'],
                    'description': r['description'],
                    'resource_type': r['resource_type'],
                    'success_rate': sr,
                    'reason': f"Alternative for '{failed_tool}': {r['description'][:50] if r['description'] else 'no description'}"
                })
        
        return alternatives
    
    def _generate_diagnosis(self, failed_tool: str, error: str, error_type: str) -> str:
        """Generate human-readable diagnosis"""
        
        diagnoses = {
            'null_none': f"The tool '{failed_tool}' likely encountered a None/null value. "
                         f"This indicates missing validation or unexpected state.",
            'type_error': f"Type mismatch in '{failed_tool}'. "
                         f"Check input types match expected schema.",
            'import_error': f"'{failed_tool}' depends on missing modules. "
                           f"Check dependency installation.",
            'permission': f"'{failed_tool}' lacks required permissions or access rights.",
            'timeout': f"'{failed_tool}' timed out. Consider increasing timeout "
                       f"or checking service availability.",
            'not_found': f"'{failed_tool}' references missing resources or endpoints.",
            'syntax': f"'{failed_tool}' has syntax errors. Check code structure.",
            'memory': f"'{failed_tool}' exhausted memory. Consider optimization.",
            'network': f"'{failed_tool}' has network issues. Check connectivity.",
        }
        
        return diagnoses.get(error_type, f"'{failed_tool}' failed with: {error[:100]}")
    
    def _generate_learned_approach(self, error_type: str, failed_tool: str,
                                  alternatives: List[Dict]) -> str:
        """Generate strategy for next attempt"""
        
        approach = f"Try tool(s) that handle '{error_type}' errors: "
        if alternatives:
            names = [a['name'] for a in alternatives[:3]]
            approach += ", ".join(names)
        else:
            approach += "No direct alternatives found. Consider manual intervention."
        
        return approach
    
    def execute_with_retry(self, task: str, initial_candidates: List[Dict]) -> Dict:
        """
        Execute task with self-reflection retry loop.
        
        Algorithm from AnyTool paper:
        WHILE attempts < MAX_RETRIES:
            1. Execute selected tool
            2. IF success → return
            3. ELSE → reflect, get alternatives, retry
        """
        attempts = []
        
        for attempt in range(self.max_retries):
            # Select candidate
            if attempt == 0:
                # First attempt - use best initial candidate
                candidate = initial_candidates[0] if initial_candidates else None
            else:
                # Subsequent attempts - use reflection result
                prev_failed = attempts[attempt - 1]['tool']
                prev_error = attempts[attempt - 1]['error']
                
                reflection = self.reflect(
                    failed_tool=prev_failed,
                    error=prev_error,
                    query=task,
                    candidates=initial_candidates
                )
                
                if not reflection.should_retry:
                    break
                
                # Select best alternative
                if reflection.alternative_tools:
                    alt_name = reflection.alternative_tools[0]
                    candidate = self._get_candidate_by_name(alt_name, initial_candidates)
                else:
                    candidate = None
            
            if not candidate:
                return self._format_failure(task, attempts)
            
            # Execute (simulated)
            result = self._simulate_execution(candidate['name'], task)
            
            attempts.append({
                'attempt': attempt + 1,
                'tool': candidate['name'],
                'result': result.get('success', False),
                'error': result.get('error', ''),
                'output': result.get('output', '')
            })
            
            if result.get('success'):
                return self._format_success(task, attempts)
        
        return self._format_failure(task, attempts)
    
    def _get_candidate_by_name(self, name: str, candidates: List[Dict]) -> Optional[Dict]:
        """Get candidate by name"""
        for c in candidates:
            if c.get('name') == name:
                return c
        
        # Fetch from DB
        row = self.conn.execute("""
            SELECT name, description, body_text, resource_type,
                   times_correct, times_triggered
            FROM resources
            WHERE name = ?
        """, (name,)).fetchone()
        
        if row:
            return dict(row)
        return None
    
    def _simulate_execution(self, tool_name: str, task: str) -> Dict:
        """Simulate tool execution"""
        # Get tool stats
        row = self.conn.execute("""
            SELECT times_correct, times_triggered FROM resources WHERE name = ?
        """, (tool_name,)).fetchone()
        
        if row:
            tt = row['times_triggered'] or 1
            tc = row['times_correct'] or 0
            success_prob = tc / tt if tt > 0 else 0.6
        else:
            success_prob = 0.5
        
        import random
        if random.random() < success_prob:
            return {'success': True, 'output': f"{tool_name} completed successfully"}
        else:
            return {'success': False, 'error': f"{tool_name} execution failed"}
    
    def _format_success(self, task: str, attempts: List[Dict]) -> Dict:
        """Format successful result"""
        successful = [a for a in attempts if a['result']][-1]
        
        # Log success
        self.conn.execute("""
            UPDATE resources SET times_correct = times_correct + 1,
            times_triggered = times_triggered + 1
            WHERE name = ?
        """, (successful['tool'],))
        
        # Log failure for failed attempts
        for a in attempts:
            if not a['result']:
                self.conn.execute("""
                    UPDATE resources SET times_wrong = times_wrong + 1,
                    times_triggered = times_triggered + 1
                    WHERE name = ?
                """, (a['tool'],))
        
        self.conn.commit()
        
        return {
            'status': 'success',
            'task': task,
            'attempts': len(attempts),
            'tool_used': successful['tool'],
            'message': f"Succeeded on attempt {successful['attempt']}"
        }
    
    def _format_failure(self, task: str, attempts: List[Dict]) -> Dict:
        """Format failure result"""
        return {
            'status': 'failed',
            'task': task,
            'attempts_made': len(attempts),
            'tools_tried': [a['tool'] for a in attempts],
            'message': f"Failed after {len(attempts)} attempts",
            'recommendation': "Manual intervention required"
        }
    
    def close(self):
        self.conn.close()


def demo():
    reflector = SelfReflector()
    
    print("="*60)
    print("  SELF-REFLECTION DEMO")
    print("="*60)
    
    # Simulate a failure scenario
    reflection = reflector.reflect(
        failed_tool="investigate",
        error="TypeError: 'NoneType' object has no attribute 'split'",
        query="debug Python login function returning None",
        candidates=[
            {'name': 'investigate', 'description': 'Debug tool'},
            {'name': 'debug', 'description': 'Debug tool'},
        ]
    )
    
    print(f"\n📋 Diagnosis: {reflection.diagnosis}")
    print(f"🔄 Should retry: {reflection.should_retry}")
    print(f"🔧 Alternatives: {reflection.alternative_tools}")
    print(f"📝 Learned: {reflection.learned_approach}")


if __name__ == "__main__":
    demo()