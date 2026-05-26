# LeadIQ Frontend-Backend Wiring — Execution Plan

## State Analysis

**Current state:**
- Backend has 31 collectors producing real data (6+ verified working)
- Services layer complete: CrawlerReliability, JobLiveness, JobLeadQuality, SourcePatternLearner, SourceMetrics, CollectorHealth
- Frontend has pages for Overview, Pipeline, DemandMiner, CommandCenter, ROITracker, Schemes, Funding, Jobs
- Frontend communicates via Next.js API proxy routes with demo data fallback
- Real-time via SSE (useLiveFeed hook)
- 7 wiring gaps identified

**Target state:** Every collector feeds into the correct frontend page with real-time updates, source badges, and health monitoring.

---

## Typed DAG Plan

```yaml
plan_id: "leadiq-frontend-backend-wiring"
goal: "Wire all 31 backend collectors into frontend pages with real-time updates"
resources_mapped:
  - "[[topics/collector-architecture]]"
  - "[[topics/nextjs-proxy-pattern]]"
  - "[[topics/sse-real-time]]"
  - "[[topics/source-metrics-dashboard]]"
created: "2026-05-12"
phases:
```

### Phase 1: Jobs Aggregate API
**Type:** implement | **Dependencies:** None | **Checkpoint:** yes

**What:** Create an aggregated jobs endpoint that merges results from all job collectors (Indeed, Internshala, Naukri, Shine, Cutshort, Foundit, etc.)

**Backend:**
```
New: backend/api/jobs.py
  GET /api/jobs/all?q=frontend&location=remote&source=indeed
  → Queries JobSignal table
  → Filters by source, keyword, location
  → Returns merged results sorted by date
  → Supports ?source=indeed,internshala filter
```

**Frontend:**
```
Modify: src/views/JobSignals.tsx
  → Fetch from /api/jobs/all instead of /api/jobs/naukri
  → Add source filter dropdown (All, Indeed, Internshala, Naukri, etc.)
  → Add source badge on each job card
  → Keep existing search + work_mode filters

New: src/app/api/jobs/all/route.ts
  → Proxy to backend /api/jobs/all
  → Fallback to demo data if backend unavailable
```

**Pattern source:** The existing `/api/jobs/naukri` proxy route and `JobSignals.tsx` — extend, don't replace.

### Phase 2: Collector Health Dashboard
**Type:** implement | **Dependencies:** Phase 1 | **Checkpoint:** yes

**What:** Expose collector health as an API endpoint and integrate into the Overview page.

**Backend:**
```
Existing: backend/services/collector_health.py
  → check_all_collectors() already works

New endpoint: backend/api/health.py
  → GET /api/collectors/health
  → Calls check_all_collectors() with 10s timeout per collector
  → Returns {sources: [{source, healthy, posts, response_time_ms, error}]}
```

**Frontend:**
```
Modify: src/views/Overview.tsx
  → Add "Sources" section below stats cards
  → Green/red indicator per source
  → Posts count, response time, last seen

New: src/app/api/collectors/health/route.ts
  → Proxy to backend /api/collectors/health
  → Cache for 60s (health check is expensive)
```

**Pattern source:** CollectorHealth service (already built) + EnhancedStatsCards pattern.

### Phase 3: Real-Time Pipeline Updates via SSE
**Type:** implement | **Dependencies:** None | **Checkpoint:** yes

**What:** Wire SSE events into the pipeline board so new leads appear in real-time.

**Backend:**
```
Modify: backend/events/emitter.py
  → Ensure lead:collected events carry source, title, score
  → Add lead:stage_changed event for drag-and-drop

Already exists: /api/stream SSE endpoint
```

**Frontend:**
```
Modify: src/hooks/use-live-feed.ts
  → Add handler for lead:collected → insert into TanStack Query cache
  → Add handler for lead:stage_changed → update lead stage in cache
  → Add handler for lead:scored → update score display

Modify: src/components/enhanced/enhanced-pipeline-board.tsx
  → Listen to useLiveFeed events
  → Auto-populate "detected" column with new leads
  → Show animation when new lead arrives
```

**Pattern source:** gemma4-browser-extension askWebsite.ts (real-time content updates pattern).

### Phase 4: Profile Keywords → Collector Wiring
**Type:** implement | **Dependencies:** None | **Checkpoint:** no

**What:** Feed profile setup wizard keywords into collector queries so users get personalized results.

**Backend:**
```
Modify: backend/ingestion/collectors.py → get_collectors()
  → Accept include_keywords from profile
  → Pass keywords to job collectors (Indeed, Internshala, Naukri, etc.)
  → Already partially supports this via QueryGenerator

Already exists: User profile with include_keywords, hiring_roles, skills fields
```

**Frontend:**
```
Modify: src/views/Overview.tsx
  → On mount, check if profile is complete
  → If not, show "Set up your profile to personalize leads" banner
  → Link to ProfileSetupWizard

Modify: src/components/profile-setup-wizard.tsx
  → On complete, trigger POST /api/run-miner with profile keywords
  → Show "Collecting leads based on your preferences..." state
```

**Pattern source:** ProfileSetupWizard + useProfile hook already exist — just wire the trigger.

### Phase 5: Source Types Expansion + Badges
**Type:** implement | **Dependencies:** None | **Checkpoint:** no

**What:** Add all 31 source types to the frontend type system so badges/icons display correctly.

**Frontend:**
```
Modify: src/types/lead.ts
  → Expand LeadSource type:
    type LeadSource = 'reddit' | 'linkedin' | 'x' | 'yc' | 'indie_hackers'
    | 'producthunt' | 'stackoverflow' | 'hn' | 'twitter' | 'github' | 'rss'
    | 'github_issues' | 'telegram' | 'naukri' | 'internshala' | 'indeed'
    | 'linkedin_jobs' | 'cutshort' | 'foundit' | 'freejobalert'
    | 'freshersworld' | 'hirect' | 'hirist' | 'iimjobs' | 'instahyre'
    | 'monster' | 'naukrigulf' | 'sarkari_result' | 'shine' | 'timesjobs'
    | 'weekday' | 'employment_news' | 'angellist' | 'crunchbase'

Modify: src/components/enhanced/enhanced-lead-card.tsx
  → Add source badge for each source
  → Color-code by source category (jobs=blue, community=green, govt=orange)
```

**Pattern source:** Existing LeadSource type in types/lead.ts — extend with new values.

### Phase 6: Verification
**Type:** test | **Dependencies:** Phases 1-5 | **Checkpoint:** yes

**Commands:**
```bash
# Backend tests
uv run --project backend pytest backend/tests/services/ -q

# Frontend tests
npm test -- --testPathPattern="pages"

# Manual verification
curl http://localhost:8000/api/jobs/all?q=frontend&source=indeed,internshala
curl http://localhost:8000/api/collectors/health
```

**Acceptance criteria:**
- [ ] `/api/jobs/all` returns jobs from multiple sources
- [ ] JobSignals page shows jobs from all sources with badges
- [ ] Overview shows collector health indicators
- [ ] Pipeline board updates in real-time via SSE
- [ ] Profile keywords feed into collector queries
- [ ] All 31 source types render correctly

---

## Resource Map

| Phase | NEXUS Resource | Pattern Source | Files |
|-------|---------------|----------------|-------|
| 1 | `[[topics/jobs-aggregate-api]]` | Existing `/api/jobs/naukri` proxy | `backend/api/jobs.py`, `src/app/api/jobs/all/route.ts`, `src/views/JobSignals.tsx` |
| 2 | `[[topics/collector-health]]` | `collector_health.py` + `EnhancedStatsCards` | `backend/api/health.py`, `src/views/Overview.tsx` |
| 3 | `[[topics/sse-real-time]]` | `useLiveFeed` + `enhanced-pipeline-board` | `src/hooks/use-live-feed.ts`, `src/components/enhanced/enhanced-pipeline-board.tsx` |
| 4 | `[[topics/profile-keywords]]` | `ProfileSetupWizard` + `QueryGenerator` | `backend/ingestion/collectors.py`, `src/views/Overview.tsx` |
| 5 | `[[topics/source-types]]` | `src/types/lead.ts` LeadSource type | `src/types/lead.ts`, `src/components/enhanced/enhanced-lead-card.tsx` |
| 6 | `[[topics/verification-loop]]` | pytest + npm test | All files |

---

## Build Order

```
Phase 1 (Jobs API) ──────┐
                          ├──> Phase 6 (Verify)
Phase 2 (Health) ────────┤
                          │
Phase 3 (SSE) ───────────┤
                          │
Phase 4 (Keywords) ──────┘
                          │
Phase 5 (Source types) ──┘
```

**Parallel execution:** Phases 1-5 can run in parallel (no cross-dependencies on code changes, only on the final integration).
