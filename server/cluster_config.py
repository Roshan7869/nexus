"""NEXUS v4 Domain Cluster Definitions.

7 domain clusters + 1 general fallback. Each cluster groups resources
by domain relevance across all resource types (skills, agents, commands,
MCP tools, etc.). Cluster routing narrows the FAISS search space from
1,097 resources to ~150-200 per activated cluster.
"""

CLUSTER_DEFINITIONS = {
    "analyzer_planner": {
        "display_name": "Analyzer & Planner",
        "description": "Reasoning, planning, analysis, review, research, strategic thinking, plan execution, and autonomous health orchestration via adaptive-imagining-cat + nexus-plan-executor",
        "trigger_keywords": [
            "analyze", "plan", "review", "audit", "research", "investigate",
            "benchmark", "strategy", "retro", "assessment", "diagnose",
            "evaluate", "measure", "optimize", "plan-eng", "plan-design",
            "plan-ceo", "code review", "performance", "token", "inspect",
            "examine", "compare", "assess", "report", "insight",
            "health", "repair", "integrity", "fix nexus", "orchestrate",
            "cluster mapping", "recalibrate", "auto-repair", "heal",
            "ruflo", "swarm", "memory", "hooks", "agent spawn", "orchestration",
            "gstack review", "gstack investigate", "gstack retro",
            "plan executor", "resume plan", "checkpoint", "long running",
            "phase execution", "task tracking", "subtask", "graphify",
            "nexus executor", "plan tracking", "auto-resume",
        ],
        "resource_types": ["skill", "agent", "command", "hook", "mcp", "cli_tool"],
        "name_patterns": [
            r"(?:plan|review|analy|investig|bench|retro|strateg|audit|eval|diagnos|measure|optim)",
            r"(?:health-orchestrat|nexus-health|auto-repair|cluster-map)",
            r"(?:office-hours|search-first|iterative-retrieval|context-restore)",
            r"(?:gstack-review|gstack-stats|code-review|review-pr)",
            r"(?:adaptive-imagining|orchestrat|ruflo)",
            r"(?:swarm-|memory-|hooks-|agent-spawn|ruflo-mcp-)",
            r"(?:nexus-plan-executor|plan-executor|checkpoint-resume|graphify-track)",
        ],
        "default_skill": "adaptive-imagining-cat",
        "default_agents": ["researcher", "code-reviewer", "hierarchical-coordinator", "mesh-coordinator", "plan-executor"],
        "auto_activate_threshold": 0.35,
        "expected_size": 170,
    },
    "architect": {
        "display_name": "Architect",
        "description": "System design, architecture decisions, specs, DDD, and resource mapping",
        "trigger_keywords": [
            "architecture", "design", "system design", "ADR", "spec", "DDD",
            "domain-driven", "structure", "architect", "autopilot", "implementation",
            "v3-core", "v3-ddd", "v3-integration", "v3-security", "blueprint",
            "pattern", "model", "schema design", "bounded context",
            "ruflo ADR", "swarm architecture", "multi-agent", "agent topology",
        ],
        "resource_types": ["skill", "agent", "spec", "doc", "command"],
        "name_patterns": [
            r"(?:archit|spec-|ddd|design-|v3-core|v3-ddd|v3-integ|v3-secur|v3-memory|v3-perf|v3-swarm|v3-cli)",
            r"(?:adaptive-imagining|autopilot|autoplan|sparc-methodology|incremental)",
            r"(?:spec-architecture|spec-critique)",
            r"(?:ruflo-mcp|ruflo-spec|ruflo-agent|ruflo-design)",
        ],
        "default_skill": "autopilot",
        "default_agents": ["architect", "specification", "system-architect"],
        "auto_activate_threshold": 0.35,
        "expected_size": 100,
    },
    "frontend_ui": {
        "display_name": "Frontend & UI",
        "description": "UI/UX, design systems, components, styling, accessibility, and frontend patterns",
        "trigger_keywords": [
            "UI", "UX", "frontend", "component", "CSS", "design system", "layout",
            "styling", "responsive", "accessibility", "animation", "React", "Next",
            "wireframe", "prototype", "dashboard", "landing page", "slide", "deck",
            "HTML", "Tailwind", "figma", "button", "modal", "form",
            "gstack browse", "design shotgun", "design review", "design consultation",
            "gstack design", "design html",
        ],
        "resource_types": ["skill", "design_system", "agent", "command", "craft"],
        "name_patterns": [
            r"(?:ds-|skill-html|skill-web|skill-ppt|skill-dashboard|skill-landing|skill-prototype)",
            r"(?:frontend|ui-|component|tailwind|design-html|hyperstyle|liquid-glass|mobile-respons)",
            r"(?:admin-dashboard|browser-test|connect-chrome|setup-browser|browse)",
            r"(?:gstack-browse|gstack-design|design-shotgun|design-consult)",
        ],
        "default_skill": "frontend-ui-engineering",
        "default_agents": ["designer"],
        "auto_activate_threshold": 0.35,
        "expected_size": 260,
    },
    "backend_api": {
        "display_name": "Backend & API",
        "description": "APIs, databases, auth, middleware, server-side logic, and data patterns",
        "trigger_keywords": [
            "API", "endpoint", "database", "auth", "middleware", "server", "schema",
            "migration", "Supabase", "backend", "Django", "Spring", "PostgreSQL",
            "SQL", "ORM", "REST", "GraphQL", "RLS", "payment", "model",
            "serializer", "view", "controller", "route",
            "ruflo MCP", "MCP server", "tool registry", "MCP transport",
            "MCP sync", "MCP federation", "connection pool",
        ],
        "resource_types": ["skill", "agent", "command", "hook", "mcp", "cli_tool"],
        "name_patterns": [
            r"(?:api-|backend-|database-|postgres-|jpa-|spring|django-|supabase|auth|middleware)",
            r"(?:payment|razorpay|cashfree|booking|room-management|food-mess)",
            r"(?:aashiyaana-api|aashiyaana-auth|aashiyaana-database)",
            r"(?:ruflo-mcp-|mcp-tools|mcp-server|mcp-registry|tool-registry)",
        ],
        "default_skill": "backend-patterns",
        "default_agents": ["database-admin", "backend-dev"],
        "auto_activate_threshold": 0.35,
        "expected_size": 140,
    },
    "devops_infra": {
        "display_name": "DevOps & Infrastructure",
        "description": "CI/CD, deployment, Docker, Kubernetes, monitoring, and infrastructure",
        "trigger_keywords": [
            "deploy", "ship", "CI/CD", "Docker", "Kubernetes", "infrastructure",
            "monitoring", "canary", "release", "devops", "Vercel", "gstack",
            "setup", "teardown", "config", "health check", "pipeline",
            "staging", "production", "container", "nginx",
            "gstack ship", "gstack deploy", "gstack setup", "gstack upgrade",
            "land and deploy", "canary check",
        ],
        "resource_types": ["skill", "agent", "command", "cli_tool", "mcp"],
        "name_patterns": [
            r"(?:deploy|ship|canary|gstack|setup-deploy|ci-cd|docker|vercel|health)",
            r"(?:land-and-deploy|gstack-upgrade|ops-|config-shared|config-backtest|config-live)",
            r"(?:gstack-ship|gstack-canary|gstack-land)",
        ],
        "default_skill": "ship",
        "default_agents": ["performance-engineer"],
        "auto_activate_threshold": 0.35,
        "expected_size": 100,
    },
    "quality_security": {
        "display_name": "Quality & Security",
        "description": "Testing, QA, verification, security scanning, TDD, and coverage",
        "trigger_keywords": [
            "test", "QA", "verify", "coverage", "security scan", "lint", "TDD",
            "e2e", "integration test", "unit test", "verification", "quality",
            "security audit", "hardening", "guard", "OWASP", "XSS", "CSRF",
            "vulnerability", "penetration", "fuzz",
            "gstack qa", "gstack test", "gstack review", "gstack security",
            "gstack verify", "gstack health",
            "ruflo security", "aidefence", "red team",
        ],
        "resource_types": ["skill", "agent", "command", "hook"],
        "name_patterns": [
            r"(?:test|qa|verif|security-|guard|e2e|tdd|lint|cover|quality|django-verif|spring-verif)",
            r"(?:cpp-testing|golang-testing|python-testing|security-scan|security-review|review-pr)",
            r"(?:gstack-qa|gstack-review|gstack-health|aidefence|red-team)",
        ],
        "default_skill": "qa",
        "default_agents": ["qa-engineer", "tdd-guide", "security-architect", "security-auditor"],
        "auto_activate_threshold": 0.35,
        "expected_size": 100,
    },
    "knowledge_wiki": {
        "display_name": "Knowledge & Wiki",
        "description": "Documentation, wikis, learning, memory, context management, and content",
        "trigger_keywords": [
            "document", "wiki", "learn", "memory", "knowledge", "search",
            "context", "article", "writing", "content", "compile", "ingestion",
            "vault", "README", "CHANGELOG", "notes", "summarize",
            "ruflo memory", "memory store", "memory search", "learning note",
            "gstack learn", "gstack document",
        ],
        "resource_types": ["skill", "doc", "craft", "agent", "command", "hook"],
        "name_patterns": [
            r"(?:wiki-|doc-|learn|context-|memory|content-|article|document-|skill-stocktake|tag-garden)",
            r"(?:craft-|C\d+-|ingestion|vault|compile|skill-doc|skill-author)",
            r"(?:ruflo-memory|gstack-learn|memory-store|memory-search)",
        ],
        "default_skill": "learn",
        "default_agents": [],
        "auto_activate_threshold": 0.35,
        "expected_size": 160,
    },
    "general": {
        "display_name": "General & Cross-Cutting",
        "description": "Resources that span multiple domains or don't fit a specific cluster",
        "trigger_keywords": [],
        "resource_types": [
            "skill", "agent", "command", "hook", "cli_tool", "mcp",
            "design_system", "craft", "doc", "spec",
        ],
        "name_patterns": [],
        "default_skill": "adaptive-imagining-cat",
        "default_agents": [],
        "auto_activate_threshold": 0.20,
        "expected_size": 200,
    },
}

# Cluster display order for listing
CLUSTER_ORDER = [
    "analyzer_planner",
    "architect",
    "frontend_ui",
    "backend_api",
    "devops_infra",
    "quality_security",
    "knowledge_wiki",
    "general",
]

# Default skill mapping: which skill activates for each cluster
DEFAULT_SKILL_MAP = {
    name: config["default_skill"]
    for name, config in CLUSTER_DEFINITIONS.items()
}

# Default agent mapping: which agents activate for each cluster
DEFAULT_AGENT_MAP = {
    name: config["default_agents"]
    for name, config in CLUSTER_DEFINITIONS.items()
}
