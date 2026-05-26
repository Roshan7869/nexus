# LeadIQ High-Conversion Lead Engine: Execution Plan
# Generated: 2026-05-13
# Synthesis from: Scrapling, ScrapeCraft, Sherlock, NEXUS research + LeadIQ audit

================================================================================
PHASE 1: FOUNDATION FIXES (Weeks 1-3) — "Unblock the Pipeline"
================================================================================

## Task 1.1: Fix RawPost Missing Fields (CRITICAL)
File: backend/collectors/base.py
Issue: RawPost missing email, company_size, funding_stage, revenue, decision_maker

ADD to RawPost dataclass:
```python
email: str = ""
company_name: str = ""
company_domain: str = ""
company_size: str = ""
funding_stage: str = ""
revenue_estimate: str = ""
decision_maker: bool = False
seniority: str = ""
department: str = ""
linkedin_url: str = ""
twitter_url: str = ""
github_url: str = ""
phone: str = ""
location: str = ""
country: str = ""
tech_stack: list[str] = field(default_factory=list)
intent_signals: list[str] = field(default_factory=list)
```

## Task 1.2: Wire Deduplication (CRITICAL)
File: backend/services/pipeline_service.py
Issue: dedup_service.find_duplicate() exists but never called

ADD after collect():
```python
deduped_posts = []
for post in raw_posts:
    existing = await self.dedup_service.find_duplicate(
        content_hash=post.content_hash,
        source=post.source
    )
    if not existing:
        deduped_posts.append(post)
```

## Task 1.3: Fix NEXUS Gateway B1 (CRITICAL)
File: nexus/server/provider.py
Issue: provider.complete() should call chat_completions()

FIX: Ensure all LLM providers proxy via /v1/chat/completions not native APIs

## Task 1.4: Increase Enrichment Quotas (HIGH)
File: backend/services/enrichment.py
Issue: Hunter.io = 25/day, Clearbit = 50/day (exhausted quickly)

ACTION: Upgrade to professional tier (Hunter.io 500/day, Clearbit 500/day)

## Task 1.5: Replace Stub Scoring Models (HIGH)
File: backend/ml/rl_scorer.py, backend/ml/uplift_scorer.py
Issue: Both return 0.5 always (45% of ensemble is random)

ACTION: Replace with trained RL model or causal uplift modeling

================================================================================
PHASE 2: INTELLIGENCE LAYER (Weeks 4-6) — "Learn From Data"
================================================================================

## Task 2.1: Build Pattern Learner
New File: backend/services/pattern_learner.py
Forked from: scrapecraft/backend/app/services/pattern_learner.py

Track per-domain:
- selectors_used
- success_rate
- avg_extraction_time
- common_errors
- optimal_delay
- retry_strategy

## Task 2.2: Build Collector Agent
New File: backend/agents/collector_agent.py
Forked from: scrapecraft/backend/app/agents/scraping_agent.py

LangGraph workflow:
1. Classify URL type (job board, social, forum)
2. Select fetcher (Static/Stealthy/Dynamic)
3. Apply selector schema
4. Fallback to LLM-guided extraction

## Task 2.3: Add Social Discovery
New File: backend/collectors/social_discovery.py
Forked from: sherlock/sherlock_project/

Given name + company → find LinkedIn, Twitter, GitHub, personal blog

## Task 2.4: Expand Confidence Formula
File: backend/services/confidence.py
Add: company_domain, revenue, decision_maker, tech_stack, intent_signals

## Task 2.5: Add Rate Limiting + Proxy Rotation
New File: backend/services/proxy_manager.py
Forked from: Scrapling/scrapling/engines/toolbelt/proxy_rotation.py

Implement:
- ProxyRotator with cyclic rotation
- Global rate limiting per source
- Browser fingerprinting (browserforge)

================================================================================
PHASE 3: QUALITY LAYER (Weeks 7-8) — "Close The Gaps"
================================================================================

## Task 3.1: Real Score Display
File: src/components/LeadCard.tsx
Show actual composite score (0-100) not mapped dummy values

## Task 3.2: ICP Fit Visualization
File: src/components/LeadCard.tsx
Show ICP match % (engineer vs VP, startup vs enterprise)

## Task 3.3: Fail-Closed Enrichment
File: backend/services/enrichment.py
Waterfall should fail + retry, not fail-open when Redis unavailable

## Task 3.4: Wire Feedback Loop
File: backend/services/feedback_loop.py
Connect enrichment outcomes → scoring model retraining

## Task 3.5: Observability
File: nexus/server/server.py
Wire NEXUS properly → get Helicone-style logging for free

================================================================================
PRIORITY ORDER (by ROI)
================================================================================

1. Task 1.1: RawPost fields — Without email+decision_maker = unconvertable
2. Task 1.2: Dedup wiring — Stop collecting duplicates
3. Task 1.3: NEXUS B1 — Gateway must work for everything else
4. Task 2.4: Confidence formula — Better lead quality scoring
5. Task 2.5: Proxy rotation — Reduce blocks, increase coverage
6. Task 1.5: Stub scorers — Remove 45% random noise
7. Task 2.1: Pattern learner — Remember what works
8. Task 2.2: Collector agent — Adaptive scraping
9. Task 3.1: Frontend scores — See real quality
10. Task 2.3: Social discovery — Enrich with social proof

================================================================================
FILES TO MODIFY/CREATE
================================================================================

MODIFY:
- backend/collectors/base.py (Task 1.1)
- backend/services/pipeline_service.py (Task 1.2)
- nexus/server/provider.py (Task 1.3)
- backend/services/confidence.py (Task 2.4)
- src/components/LeadCard.tsx (Task 3.1, 3.2)
- backend/services/enrichment.py (Task 1.4, 3.3)
- backend/ml/rl_scorer.py (Task 1.5)
- backend/ml/uplift_scorer.py (Task 1.5)
- nexus/server/server.py (Task 3.5)

CREATE:
- backend/services/pattern_learner.py (Task 2.1)
- backend/agents/collector_agent.py (Task 2.2)
- backend/collectors/social_discovery.py (Task 2.3)
- backend/services/proxy_manager.py (Task 2.5)
- backend/services/feedback_loop.py (Task 3.4)

================================================================================
