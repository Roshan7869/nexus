from typing import TypedDict, NamedTuple, Literal

from server.context_harness.project_types import ProjectType


class BundleCriteria(TypedDict):
    resource_types: list[str]
    exclude_types: list[str]
    project_type: ProjectType | None
    keyword_include: list[str]
    keyword_exclude: list[str]
    cluster_include: list[str]
    min_tier: int
    max_resources: int | None
    priority_base: int
    is_canonical_when: Literal['primary_type', 'type_plus_keyword', 'always', 'never']


class BundleDefinition(NamedTuple):
    name: str
    kind: str
    description: str
    criteria: BundleCriteria
    default_priority: int


BundleRegistry = dict[str, BundleDefinition]


_BUNDLES: BundleRegistry = {
    'core_infra': BundleDefinition(
        name='core_infra',
        kind='core_infra',
        description='Core infrastructure resources: skills, agents, MCP tools, and commands.',
        criteria=BundleCriteria(
            resource_types=['skill', 'agent', 'mcp', 'command'],
            exclude_types=[],
            project_type=None,
            keyword_include=[],
            keyword_exclude=['deprecated', 'legacy'],
            cluster_include=[],
            min_tier=1,
            max_resources=None,
            priority_base=10,
            is_canonical_when='never',
        ),
        default_priority=10,
    ),
    'ai_ml_bundle': BundleDefinition(
        name='ai_ml_bundle',
        kind='project_type',
        description='AI/ML project resources: models, embeddings, LLMs, and neural systems.',
        criteria=BundleCriteria(
            resource_types=[],
            exclude_types=[],
            project_type=ProjectType.AI_ML,
            keyword_include=['ml', 'ai', 'tensorflow', 'pytorch', 'llm', 'rag', 'embedding', 'neural'],
            keyword_exclude=[],
            cluster_include=['general', 'quality_security', 'analyzer_planner'],
            min_tier=2,
            max_resources=500,
            priority_base=30,
            is_canonical_when='primary_type',
        ),
        default_priority=30,
    ),
    'web_dev_bundle': BundleDefinition(
        name='web_dev_bundle',
        kind='project_type',
        description='Web development resources: frontend frameworks, APIs, and design systems.',
        criteria=BundleCriteria(
            resource_types=['skill', 'agent', 'design_system', 'mcp'],
            exclude_types=[],
            project_type=ProjectType.WEB_DEV,
            keyword_include=['react', 'vue', 'angular', 'css', 'html', 'api', 'web', 'frontend', 'typescript', 'javascript'],
            keyword_exclude=[],
            cluster_include=['frontend_ui', 'backend_api', 'general'],
            min_tier=2,
            max_resources=500,
            priority_base=30,
            is_canonical_when='primary_type',
        ),
        default_priority=30,
    ),
    'general_dev_bundle': BundleDefinition(
        name='general_dev_bundle',
        kind='project_type',
        description='General development catch-all bundle for all remaining resources.',
        criteria=BundleCriteria(
            resource_types=[],
            exclude_types=[],
            project_type=ProjectType.GENERAL_DEV,
            keyword_include=[],
            keyword_exclude=[],
            cluster_include=[],
            min_tier=2,
            max_resources=600,
            priority_base=50,
            is_canonical_when='never',
        ),
        default_priority=50,
    ),
    'research_bundle': BundleDefinition(
        name='research_bundle',
        kind='project_type',
        description='Research-oriented resources: papers, benchmarks, surveys, and academic content.',
        criteria=BundleCriteria(
            resource_types=[],
            exclude_types=['cli_tool', 'design_system'],
            project_type=ProjectType.RESEARCH,
            keyword_include=['paper', 'research', 'analysis', 'study', 'benchmark', 'survey', 'methodology'],
            keyword_exclude=[],
            cluster_include=['analyzer_planner', 'knowledge_wiki', 'general'],
            min_tier=2,
            max_resources=300,
            priority_base=40,
            is_canonical_when='primary_type',
        ),
        default_priority=40,
    ),
    'devops_bundle': BundleDefinition(
        name='devops_bundle',
        kind='project_type',
        description='DevOps and infrastructure resources: containers, CI/CD, monitoring, and cloud.',
        criteria=BundleCriteria(
            resource_types=['skill', 'command', 'cli_tool', 'mcp'],
            exclude_types=[],
            project_type=ProjectType.DEVOPS,
            keyword_include=['docker', 'deploy', 'ci/cd', 'kubernetes', 'monitoring', 'infrastructure', 'terraform'],
            keyword_exclude=[],
            cluster_include=['devops_infra', 'quality_security', 'general'],
            min_tier=2,
            max_resources=400,
            priority_base=30,
            is_canonical_when='primary_type',
        ),
        default_priority=30,
    ),
    'trading_bundle': BundleDefinition(
        name='trading_bundle',
        kind='project_type',
        description='Trading and market resources: strategies, indicators, crypto, and risk management.',
        criteria=BundleCriteria(
            resource_types=[],
            exclude_types=['design_system'],
            project_type=ProjectType.TRADING,
            keyword_include=['trade', 'market', 'stock', 'crypto', 'strategy', 'freqtrade', 'indicator'],
            keyword_exclude=[],
            cluster_include=['general', 'analyzer_planner', 'architect'],
            min_tier=2,
            max_resources=200,
            priority_base=30,
            is_canonical_when='primary_type',
        ),
        default_priority=30,
    ),
}


def get_bundles() -> BundleRegistry:
    return dict(_BUNDLES)


def get_bundle(name: str) -> BundleDefinition | None:
    return _BUNDLES.get(name)


def _concat_resource_text(resource: dict) -> str:
    parts = []
    for key in ('name', 'description', 'body_text'):
        val = resource.get(key)
        if val:
            parts.append(str(val))
    return ' '.join(parts)


def _has_any_keyword(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return False
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def match_criteria(
    resource: dict,
    classification: dict | None,
    criteria: BundleCriteria,
) -> bool:
    rt = criteria['resource_types']
    if rt and resource.get('resource_type') not in rt:
        return False

    if resource.get('resource_type') in criteria['exclude_types']:
        return False

    pt = criteria['project_type']
    if pt is not None:
        if classification is None:
            return False
        if classification.get('project_type') != pt:
            return False

    ki = criteria['keyword_include']
    if ki:
        text = _concat_resource_text(resource)
        if not _has_any_keyword(text, ki):
            return False

    ke = criteria['keyword_exclude']
    if ke:
        text = _concat_resource_text(resource)
        if _has_any_keyword(text, ke):
            return False

    ci = criteria['cluster_include']
    if ci and resource.get('cluster') not in ci:
        return False

    if resource.get('tier', 0) < criteria['min_tier']:
        return False

    return True


def get_canonical_bundle(
    resource: dict,
    classifications: dict[int, dict],
    all_defs: BundleRegistry,
) -> str | None:
    resource_id = resource.get('id')
    if resource_id is None:
        return None

    classification = classifications.get(resource_id)

    for name, defn in all_defs.items():
        when = defn.criteria['is_canonical_when']

        if when == 'never':
            continue

        if when == 'always':
            return name

        pt = defn.criteria['project_type']
        if pt is None:
            continue
        if classification is None or classification.get('project_type') != pt:
            continue

        if when == 'primary_type':
            return name

        if when == 'type_plus_keyword':
            ki = defn.criteria['keyword_include']
            if not ki:
                continue
            text = _concat_resource_text(resource)
            if _has_any_keyword(text, ki):
                return name

    return None
