#!/usr/bin/env python3
"""Enrich all skills with short body_text by reading actual SKILL.md files from disk."""
import sys, os, sqlite3, re
from pathlib import Path

sys.path.insert(0, str(Path.home() / "nexus/server"))

DB_PATH = str(Path.home() / "nexus/db/nexus.db")

# ── Keyword pool per skill name (for skills without SKILL.md) ──
KEYWORD_MAP = {
    "investigate": "debug fix bug investigate root cause analysis error crash troubleshooting regression why is this broken stack trace log analysis",
    "security-review": "security audit penetration testing authentication authorization input validation secrets management OWASP threat model vulnerability scan",
    "content-engine": "write blog post content creation social media posts newsletter platform-native content repurposed campaigns editorial calendar",
    "article-writing": "write article blog post guide tutorial long-form content newsletter issue polished written content voice consistency",
    "review": "code review pull request PR review diff analysis diff review pre-landing check SQL safety condition side effects structural issues",
    "e2e-testing": "end-to-end testing Playwright E2E test browser automation page object model test configuration CI CD integration flaky test",
    "tdd-workflow": "test-driven development TDD write tests first unit test integration test 80% coverage red-green-refactor test framework",
    "api-design": "REST API design API endpoint API specification OpenAPI schema route design request validation response format",
    "github-code-review": "GitHub code review pull request review swarm coordination reviewed code merge check automated review",
    "plan-ceo-review": "CEO review strategy review product review scope analysis founder mode rethink problem expand scope find 10x product",
    "plan-eng-review": "engineering review architecture review design doc review execution plan lock in plan technical review data flow edge cases",
    "plan-design-review": "design review visual design UX critique UI plan review product design design system evaluate mockup",
    "postgres-patterns": "PostgreSQL database query optimization schema design indexing Supabase best practices performance SQL queries",
    "jpa-patterns": "JPA Hibernate entity design relationship mapping query optimization Spring Boot transaction auditing pagination",
    "database-migrations": "database migration schema change data migration rollback zero-downtime deployment PostgreSQL MySQL Prisma Drizzle",
    "deployment-patterns": "deployment workflow CI CD pipeline Docker container health check rollback strategy production readiness",
    "clickhouse-io": "ClickHouse database analytics data engineering high-performance analytical workload query optimization columnar storage",
    "pair-programming": "pair programming driver navigator TDD real-time verification code review security refactoring truth-score verification",
    "skills": "Claude Code skills YAML frontmatter skill specification skill template skills directory skill development skill publishing",
    "claude": "Claude Code CLI wrapper diff review adversarial failure-mode testing consult second opinion from Claude AI review",
    "codex": "OpenAI Codex CLI code review adversarial review consult second opinion AI assistant code analysis alternative perspective",
    "scrape": "web scraping data extraction page data pull structured data from web page scraping intent HTML parsing data gathering",
    "skillify": "codify scrape flow persistent browser skill scrape script synthesis test generation fixture save scrape as permanent skill",
    "browse": "headless browser QA testing site dogfooding navigation element interaction screenshot responsive testing form dialog upload",
    "qa": "QA test web application bug finding fix verification health score before-after screenshots ship-readiness test-fix-verify loop",
    "qa-only": "report-only QA testing bug report health score screenshots repro steps structured test report no code changes",
    "ship": "ship workflow merge base branch run tests review diff bump version update changelog commit push create pull request",
    "health": "code quality dashboard type checker linter test runner dead code detection composite score 0-10 trend tracking quality metrics",
    "setup-deploy": "configure deployment settings deploy platform production URL health check endpoint deploy status command land-and-deploy setup",
    "document-release": "post-ship documentation update README ARCHITECTURE CONTRIBUTING AGENTS CHANGELOG version bump todo cleanup sync docs",
    "land-and-deploy": "merge PR wait for CI deploy verify production health canary check post-deploy verification merge and deploy workflow",
    "checkpoint": "save resume working state checkpoint git state capture decisions remaining work resume across sessions workspace handoff",
    "context-save": "save working context git state decisions remaining work session continuity context persistence state management",
    "context-restore": "restore working context saved state session resume cross-branch restore pick up where left off context recovery",
    "plan-tune": "question tuning developer psychographic question preferences never-ask always-ask profile behavioral observation survey",
    "learn": "manage project learnings review search prune export learning knowledge base cross-session memory pattern retrieval persistent",
    "retro": "weekly engineering retrospective commit history analysis work patterns code quality metrics trend tracking per-person contributions",
    "freeze": "restrict file edits to specific directory session-scoped edit barrier prevent accidental changes scoped editing isolation",
    "unfreeze": "clear freeze boundary allow all directory edits remove edit restriction unlock editing widen scope session control",
    "guard": "full safety mode destructive command warnings directory-scoped edit barrier combined protection production safety lock down",
    "careful": "safety guardrails destructive commands warning before rm -rf DROP TABLE force push git reset kubectl delete prod safety",
    "design-consultation": "design consultation product understanding landscape research design system aesthetic typography color layout spacing motion",
    "design-html": "finalize design production-quality HTML CSS Pretext-native layout dynamic text reflow zero dependency lightweight design output",
    "design-review": "visual QA design audit spacing hierarchy AI slop detection interaction quality before-after screenshot polish iterative fix",
    "design-shotgun": "design exploration generate multiple design variants comparison board structured feedback iteration visual brainstorming options",
    "plan-design-review": "design plan review visual design critique UX evaluation dimension scoring 0-10 improvement suggestions plan fix approach",
    "plan-devex-review": "developer experience plan review developer personas competitive benchmark magical moments friction points DX scoring modes",
    "devex-review": "live developer experience audit browse tool test DX navigate docs getting started flow TTHW screenshot error DX scorecard",
    "plan-ceo-review": "CEO review strategy review product review scope analysis founder mode rethink problem expand scope find 10x product",
    "office-hours": "YC Office Hours startup mode demand reality status quo specificity narrowest wedge builder mode brainstorm design thinking",
    "learn": "manage project learnings review search prune export learning knowledge base cross-session memory pattern retrieval persistent",
    "gate": "state precondition check routing guard dynamic gating tool availability conditional workflow state verification access control",
    "universal-route": "cross-type routing search ALL resource types by query find best resource regardless of type v3 multi-type search",
    "cluster-route": "domain cluster routing activate resource cluster search within cluster intentional narrowed scope cluster-aware resource search",
    "bundle": "capability bundle discovery resource group search functionality grouping feature area resource bundle find coordinated resources",
    "open-gstack-browser": "launch GStack Browser AI-controlled Chromium visible browser real-time action view sidebar activity feed anti-bot stealth",
    "pair-agent": "pair remote AI agent with browser setup key remote instruction connect share browser tab scoped access read write admin",
    "setup-browser-cookies": "import cookies from Chromium into headless browse session interactive picker domain select authenticated page QA",
    "benchmark": "performance regression page load times Core Web Vitals resource size baseline before-after comparison PR performance trend",
    "benchmark-models": "cross-model benchmark Claude GPT Gemini side-by-side latency tokens cost quality LLM judge model comparison shootout",
    "canary": "post-deploy canary monitoring live app console errors performance regression screenshot baseline comparison anomaly alert watcher",
    "hackernews-frontpage": "scrape Hacker News front page titles points comment counts news tech headlines community trending stories data extraction",
    "make-pdf": "turn markdown into publication-quality PDF proper margins page breaks page numbers cover header curly quotes em dashes TOC",
    "agentdb-vector-search": "AgentDB vector search semantic document retrieval similarity matching context querying RAG system knowledge base",
    "agentdb-memory-patterns": "AgentDB memory patterns persistent memory session storage long-term pattern learning context management stateful agents",
    "agentdb-performance-optimization": "AgentDB performance quantization memory reduction HNSW index fast search caching batch operations scaling optimization",
    "agentdb-advanced": "AgentDB advanced features QUIC synchronization multi-database custom distance hybrid search distributed systems coordination",
    "agentdb-learning": "AgentDB learning plugins reinforcement learning Decision Transformer Q-Learning SARSA Actor-Critic self-learning agent",
    "reasoningbank-intelligence": "ReasoningBank adaptive learning pattern recognition strategy optimization continuous improvement meta-cognitive system",
    "reasoningbank-agentdb": "ReasoningBank with AgentDB trajectory tracking verdict judgment memory distillation pattern recognition experience replay",
    "swarm-orchestration": "multi-agent swarm orchestration agentic-flow parallel execution dynamic topology intelligent coordination distributed AI",
    "swarm-advanced": "advanced swarm orchestration research development testing complex distributed workflow multi-agent coordination",
    "hooks-automation": "hooks automation pre-post task hooks session management Git integration memory coordination neural pattern training workflow",
    "sparc-methodology": "SPARC Specification Pseudocode Architecture Refinement Completion methodology multi-agent orchestration full dev cycle",
    "verify": "truth score code quality verification automatic rollback accuracy threshold quality assurance output verification correctness check",
    "stream-chain": "Stream-JSON chaining multi-agent pipeline data transformation sequential workflow streaming data processing chain composition",
    "verification-loop": "comprehensive verification Claude Code session accuracy check output verification quality gate automated correctness assurance",
}

def enrich_all():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    # Get all resources with tautological body_text
    rows = conn.execute("""
        SELECT id, name, file_path, description, body_text
        FROM resources
        WHERE status='active'
        AND (body_text LIKE '%skill for % tasks'
             OR (LENGTH(COALESCE(body_text, '')) < 30 AND LENGTH(COALESCE(description, '')) < 30))
        ORDER BY id
    """).fetchall()
    
    print(f"Resources needing enrichment: {len(rows)}")
    
    enriched = 0
    for r in rows:
        rid, name, file_path, desc, body = r
        new_body = None
        
        # Strategy 1: Read SKILL.md from file_path directory
        if file_path:
            fp = Path(file_path)
            if fp.is_dir():
                skill_file = fp / "SKILL.md"
                if skill_file.exists():
                    content = skill_file.read_text()
                    # Strip YAML frontmatter
                    if content.startswith("---"):
                        parts = content.split("---", 2)
                        if len(parts) >= 3:
                            # Get description from frontmatter
                            for line in parts[1].split("\n"):
                                if line.startswith("description:"):
                                    desc_from_fm = line.split(":", 1)[1].strip().strip('"').strip("'")
                                    if len(desc_from_fm) > 10:
                                        pass  # Good description
                            # Use body content (after frontmatter) for body_text
                            body_content = parts[2].strip() if len(parts) > 2 else ""
                            if len(body_content) > 50:
                                new_body = body_content[:2000]
                    else:
                        new_body = content[:2000]
        
        # Strategy 2: Use keyword map
        if not new_body:
            # Get the bare skill name (strip prefix like "claude-skill-" etc.)
            base_name = name.lower()
            for prefix in ["claude-skill-", "hermes-skill-", "gstack-", "skill-"]:
                if base_name.startswith(prefix):
                    base_name = base_name[len(prefix):]
                    break

            if base_name in KEYWORD_MAP:
                new_body = KEYWORD_MAP[base_name]
            elif len(desc or '') > 20:
                new_body = (desc or '')[:500]
            else:
                continue  # Skip if we can't enrich
        
        if new_body and len(new_body) > len(body or ''):
            conn.execute("UPDATE resources SET body_text = ? WHERE id = ?", (new_body, rid))
            enriched += 1
            if enriched <= 5:
                print(f"  Enriched id={rid:5d} name={name:35s} len={len(new_body):5d}")
    
    conn.commit()
    print(f"\nTotal enriched: {enriched}")
    conn.close()

if __name__ == "__main__":
    enrich_all()
