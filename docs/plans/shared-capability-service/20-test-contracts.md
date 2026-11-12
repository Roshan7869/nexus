# tests/test_capability_contracts.py

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Check HTTP/MCP compatibility and the exact failure modes missed by the A1 plan.

## Required contract and implementation instructions

Use an injected temporary service graph and fake providers. Never call the running daemon or production DB.
HTTP, MCP, broker and supported harness profiles share the same canonical catalog and policy. Compatibility may alter execute availability, not silently rename IDs.
Test /route, /find_skills and /nexus/route recommendation names with fixed context and seed. Assert HTTP 200, nonempty skills, exact ordered IDs, correct mode, and catalog revision.
Keep /nexus/search query/results schema and zero-write behavior. Test chat classification task_type independently.
New MCP calls must delegate once, with no duplicated provider owner and no HTTP/MCP recursion. Legacy session close remains valid.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
TEST_ENDPOINT_CONSISTENCY():
  responses=all public route adapters(query,project,seed)
  for response in responses:
    assert successful and nonempty skills
    assert canonical names and revision equal expected fixture
  assert selection list is advisory

TEST_TWO_HARNESSES():
  open Codex and Claude sessions against same temporary service
  discover extra capability outside each slate
  invoke fake shared tool in both
  assert one active provider generation and isolated sessions
```

## Acceptance checks and failure behavior

Gate all supported profile claims on actual adapter contract tests. Unsupported native actions must report UNSUPPORTED explicitly. Full existing gateway and routing regression tests must pass after integration.

## Dependencies and implementation order

Depends on 09–15.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

