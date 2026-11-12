# D3 — Use harness toolsets as a ranking boost

Source: plan §D3 · Commit: `D3: harness toolsets boost ranking scores` · Depends on: D2 · Gate: none directly (quality)

## Problem (verified)

- `server/router.py:find_matching_skills(task, project, filtered_ids, preferred_names, max_results)` — `preferred_names` **already exists** (line 417) and is used for a SQL pre-filter at `router.py:673–679` and a set at `:552–553`.
- The plan's snippet adds a `name_match_bonus`, but `task_words` is undefined in the plan. The bonus must be applied where scores are computed. **Locate the scoring loop** (after line 553) before editing.
- `server/router_v2/v5.py:unified_route_v5` — scoring function to be located; plan says apply `harness_preference_boost` to `final_score`.

## Corrections to source plan

| Plan says | Reality |
|---|---|
| Add `preferred_names` parameter to `find_matching_skills` | **Already present.** Only the boost is new. |
| `task_words` loop, then `name_match_bonus` | `task_words` not defined in the plan. Re-read the scoring code. |
| Apply boost in `v5.py` | Locate `final_score` in `v5.py` first; line numbers unverified. |
| Plan's SQL pre-filter by `preferred_names` (router.py:673) | This already exists. Check whether it *filters* (hard) or *boosts*. D3 should boost, not filter, to avoid starving results. |

## Pseudocode (corrected)

```python
# server/router.py — inside the scoring loop of find_matching_skills
preferred = {n.lower() for n in (preferred_names or [])}
HARNESS_BOOST = 0.5          # cap below; tune with golden eval
HARNESS_CAP = 0.8

for cand in candidates:                      # locate the real loop variable name
    bonus = HARNESS_BOOST if cand["name"].lower() in preferred else 0.0
    cand["score"] = cand["score"] + min(bonus, HARNESS_CAP)
```

```python
# server/router_v2/v5.py — inside unified_route_v5, where final_score is computed
harness_preference_boost = 0.3 if cand_name.lower() in preferred else 0.0
final_score += harness_preference_boost
```

Both boosts are additive and capped. Put the constants in `server/router_v2/fusion.py` next to the other blend weights (CLAUDE.md: weights live there, overridable via `NEXUS_V5_W_*`).

## Steps

1. Read the scoring loop in `router.py` (after 553) and `v5.py` `final_score`. Write exact variable names here.
2. Check whether the existing `preferred_names` SQL (router.py:673) hard-filters. If yes, change it to a boost in the same commit and note it.
3. Add boost constants to `fusion.py`.
4. Apply in both places.
5. Benchmark with golden eval; try boost values 0.0, 0.3, 0.5 and record each.

## Tests

New `tests/test_harness_boost.py`:

```python
from server.router import find_matching_skills

def test_preferred_name_outranks_same_score(seeded_two_equal_skills):
    # fixture: two skills with identical base score; one name in preferred_names
    res = find_matching_skills("probe", "nexus", None, preferred_names=["beta"])
    names = [r["name"] for r in res]
    assert names.index("beta") < names.index("alpha")

def test_no_preferred_is_unchanged(seeded_two_equal_skills):
    a = find_matching_skills("probe", "nexus", None, preferred_names=None)
    b = find_matching_skills("probe", "nexus", None, preferred_names=[])
    assert [r["name"] for r in a] == [r["name"] for r in b]
```

Benchmark: golden eval after each boost value. Keep the value with the best pass rate that does not reduce skill-match. Record all three in the commit body.

## Verification (live)

```bash
curl -s -X POST http://127.0.0.1:8000/nexus/route -H "Content-Type: application/json" \
  -d '{"task":"run tests","project":"nexus"}' | python3 -m json.tool | head -40
```

Expected: testing-related skills near the top when the harness signals testing.

## Rollback

Set `HARNESS_BOOST = 0.0` in `fusion.py`.

## Open decisions

- Final boost value — decide from the benchmark sweep, not by hand.
