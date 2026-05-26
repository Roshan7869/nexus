#!/usr/bin/env python3
"""
hierarchy_router.py — NEXUS v2 Hierarchical Tool Retrieval
Based on: AnyTool 3-Layer + ScaleMCP TDWA + Tool-to-Agent

Layer 1: Category routing (fast, ~5ms)
Layer 2: Tool retrieval within category (HNSW)
Layer 3: Tool-to-Agent join (metadata traversal)
"""

import json, sqlite3, numpy as np
from pathlib import Path
from typing import List, Dict, Tuple, Optional

DB_PATH = Path.home() / 'nexus/db/nexus.db'

# TDWA weights from ScaleMCP paper
TDWA_WEIGHTS = {
    'tool_name': 2.0,
    'synthetic_questions': 1.5,
    'description': 1.0,
    'parameters': 0.5,
}

class HierarchicalRouter:
    def __init__(self):
        self.conn = self._get_conn()
        self.categories = self._init_categories()
        
    def _get_conn(self):
        conn = sqlite3.connect(str(DB_PATH), timeout=60)
        conn.enable_load_extension(True)
        try:
            import sqlite_vec
            sqlite_vec.load(conn)
        except:
            pass
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_categories(self) -> Dict[str, List[str]]:
        """Define category hierarchy based on research"""
        return {
            "coding": ["code", "implement", "build", "create", "add", "update", "refactor", 
                      "function", "class", "api", "endpoint", "query", "schema", "bug", "fix"],
            "debug": ["debug", "error", "crash", "exception", "traceback", "fail", "issue", "problem"],
            "research": ["research", "investigate", "analyze", "explore", "find", "search", "query"],
            "design": ["design", "architecture", "spec", "plan", "structure", "pattern", "mockup"],
            "test": ["test", "qa", "verify", "coverage", "unit", "e2e", "integration", "quality"],
            "deploy": ["deploy", "ship", "release", "push", "build", "ci", "cd", "production"],
            "review": ["review", "audit", "check", "assess", "security", "performance", "refactor"],
            "learning": ["learn", "train", "model", "ml", "ai", "neural", "embedding"],
            "memory": ["memory", "store", "retrieve", "context", "remember", "persist"],
            "swarm": ["swarm", "multi-agent", "coordinate", "orchestrate", "parallel"],
        }
    
    # ═══════════════════════════════════════════════════════════════════
    # LAYER 1: Category Routing
    # ═══════════════════════════════════════════════════════════════════
    
    def route_category(self, query: str) -> List[Tuple[str, float]]:
        """Fast category selection based on query keywords"""
        query_lower = query.lower()
        words = set(query_lower.split())
        
        scores = {}
        for category, keywords in self.categories.items():
            keyword_matches = sum(1 for kw in keywords if kw in words)
            # Also check bigrams
            bigram_matches = sum(1 for i in range(len(words)) 
                              for kw in keywords 
                              if f"{list(words)[i]} {list(words)[i+1] if i+1 < len(words) else ''}" in kw)
            
            scores[category] = keyword_matches + bigram_matches * 0.5
        
        # Sort and return top categories
        sorted_cats = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [(cat, score) for cat, score in sorted_cats if score > 0][:3]
    
    # ═══════════════════════════════════════════════════════════════════
    # LAYER 2: Tool Retrieval with TDWA Embeddings
    # ═══════════════════════════════════════════════════════════════════
    
    def search_tools_tdwa(self, query: str, categories: List[str] = None, 
                         top_k: int = 5) -> List[Dict]:
        """
        Search tools using TDWA (Tool Document Weighted Average) embeddings.
        
        TDWA weights from ScaleMCP:
        - tool_name: 2.0 (highest - core identifier)
        - synthetic_questions: 1.5 (intent signals)
        - description: 1.0 (standard)
        - parameters: 0.5 (lowest - implementation detail)
        """
        
        # If no categories, detect from query
        if not categories:
            cat_scores = self.route_category(query)
            categories = [c[0] for c in cat_scores[:2]]
        
        results = []
        
        for category in categories:
            # Get tools in this category
            tools = self._get_category_tools(category, limit=20)
            
            # Score each tool using TDWA
            for tool in tools:
                tdwa_score = self._compute_tdwa(tool, query)
                tool['tdwa_score'] = tdwa_score
                tool['category_match'] = category
                results.append(tool)
        
        # Sort by TDWA score
        results.sort(key=lambda x: x.get('tdwa_score', 0), reverse=True)
        return results[:top_k]
    
    def _get_category_tools(self, category: str, limit: int = 20) -> List[Dict]:
        """Get tools matching a category"""
        rows = self.conn.execute("""
            SELECT name, description, body_text, resource_type,
                   times_triggered, times_correct, repo_name
            FROM resources
            WHERE description LIKE ? OR body_text LIKE ?
            LIMIT ?
        """, (f"%{category}%", f"%{category}%", limit)).fetchall()
        
        if len(rows) < 3:
            rows = self.conn.execute("""
                SELECT name, description, body_text, resource_type,
                       times_triggered, times_correct, repo_name
                FROM resources
                LIMIT ?
            """, (limit,)).fetchall()
        
        return [dict(r) for r in rows]
    
    def _compute_tdwa(self, tool: Dict, query: str) -> float:
        query_lower = query.lower()
        query_words = set(query_lower.split())
        
        score = 0.0
        
        name = (tool.get('name') or '').lower()
        name_words = set(name.replace('-', ' ').replace('_', ' ').split())
        name_overlap = len(query_words & name_words)
        score += name_overlap * TDWA_WEIGHTS['tool_name']
        
        desc = (tool.get('description') or '').lower()
        desc_words = set(desc.split())
        desc_overlap = len(query_words & desc_words)
        score += desc_overlap * TDWA_WEIGHTS['description']
        
        body = (tool.get('body_text') or '')[:1000].lower()
        body_words = set(body.split())
        body_overlap = len(query_words & body_words)
        score += body_overlap * TDWA_WEIGHTS['parameters']
        
        tt = tool.get('times_triggered', 0) or 0
        tc = tool.get('times_correct', 0) or 0
        sr = (tc / tt * 100) if tt > 0 else 60
        
        query_len = len(query_words) or 1
        normalized = (score / query_len) + (sr / 500)
        
        return min(normalized, 1.0)
    
    # ═══════════════════════════════════════════════════════════════════
    # LAYER 3: Tool-to-Agent Join
    # ═══════════════════════════════════════════════════════════════════
    
    def join_with_agents(self, tools: List[Dict]) -> List[Dict]:
        """
        Join tools with their parent agents/MCPs for full context.
        
        Based on Tool-to-Agent Retrieval paper:
        - Retrieve tool → traverse metadata → get parent agent
        - Combined scoring: α*tool_sim + (1-α)*agent_sim
        """
        alpha = 0.7  # Weight for tool vs agent similarity
        
        for tool in tools:
            # Find parent MCP/agent
            parent = self._find_parent_agent(tool['name'])
            
            tool['parent_agent'] = parent['name'] if parent else None
            tool['parent_type'] = parent['type'] if parent else None
            tool['parent_desc'] = parent.get('description', '') if parent else ''
            
            # Compute combined score if parent exists
            if parent:
                parent_score = self._compute_tdwa(parent, tool.get('name', ''))
                tool['combined_score'] = (alpha * tool.get('tdwa_score', 0) + 
                                         (1 - alpha) * parent_score)
            else:
                tool['combined_score'] = tool.get('tdwa_score', 0)
        
        # Re-sort by combined score
        tools.sort(key=lambda x: x.get('combined_score', 0), reverse=True)
        return tools
    
    def _find_parent_agent(self, tool_name: str) -> Optional[Dict]:
        """Find parent MCP server or agent for a tool"""
        # Look for MCP servers that might contain this tool
        mcp_servers = self.conn.execute("""
            SELECT name, description, resource_type FROM resources
            WHERE resource_type = 'mcp'
            AND (description LIKE ? OR name LIKE ?)
            LIMIT 1
        """, (f"%{tool_name}%", f"%{tool_name.split('-')[0]}%")).fetchone()
        
        if mcp_servers:
            return {'name': mcp_servers['name'], 
                   'type': mcp_servers['resource_type'],
                   'description': mcp_servers['description']}
        
        # Look for agents
        agents = self.conn.execute("""
            SELECT name, description, resource_type FROM resources
            WHERE resource_type = 'agent'
            LIMIT 5
        """).fetchall()
        
        for agent in agents:
            if any(kw in tool_name.lower() for kw in agent['name'].lower().split('-')):
                return {'name': agent['name'],
                       'type': agent['resource_type'],
                       'description': agent['description']}
        
        return None
    
    # ═══════════════════════════════════════════════════════════════════
    # MAIN ROUTING PIPELINE
    # ═══════════════════════════════════════════════════════════════════
    
    def route(self, query: str, project: str = "default") -> Dict:
        """
        Full hierarchical routing pipeline.
        
        Returns:
            - categories: Layer 1 results
            - tools: Layer 2 results (with TDWA scores)
            - tools_with_context: Layer 3 results (with parent agents)
            - final_selection: Top tool with highest combined score
        """
        # Layer 1: Category routing
        categories = self.route_category(query)
        
        # Layer 2: Tool retrieval with TDWA
        cat_names = [c[0] for c in categories]
        tools = self.search_tools_tdwa(query, cat_names, top_k=15)
        
        # Layer 3: Tool-to-Agent join
        tools_with_context = self.join_with_agents(tools)
        
        # Final selection: top tool with highest combined score
        final = tools_with_context[0] if tools_with_context else None
        
        return {
            'query': query,
            'layer1_categories': categories,
            'layer2_tools': tools[:10],
            'layer3_with_agents': tools_with_context[:5],
            'final_selection': final,
            'method': 'hierarchical_tdwa'
        }
    
    def close(self):
        self.conn.close()


def demo():
    router = HierarchicalRouter()
    
    test_queries = [
        "debug Python error in login function",
        "implement new feature for user authentication",
        "test the new API endpoint",
        "review code for security vulnerabilities",
    ]
    
    print("="*60)
    print("  HIERARCHICAL ROUTER DEMO")
    print("="*60)
    
    for query in test_queries:
        result = router.route(query)
        print(f"\n📍 Query: {query}")
        print(f"   Layer 1 (Categories): {[c[0] for c in result['layer1_categories'][:3]]}")
        print(f"   Final selection: {result['final_selection']['name'] if result['final_selection'] else 'None'}")
        print(f"   Parent agent: {result['final_selection'].get('parent_agent', 'N/A') if result['final_selection'] else 'N/A'}")
    
    router.close()


if __name__ == "__main__":
    demo()