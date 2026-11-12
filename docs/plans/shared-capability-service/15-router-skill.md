# skills/nexus-router/SKILL.md

Action: **Modify**. Status: Proposed execution instructions, design only.

## Purpose

Teach every harness to use the complete shared catalog through the same workflow.

## Required contract and implementation instructions

Replace mandatory 'Use ONLY listed tools' with this contract:
1. Start or attach a Nexus capability session.
2. Read recommendations as suggestions.
3. Search the catalog whenever another capability is needed.
4. Read complete skill instructions and selected referenced files before using a skill.
5. Invoke shared provider tools through Nexus; execute native action envelopes through the owning harness adapter.
6. Return correlated execution evidence; report unknown when evidence is absent.
7. Close the session idempotently.
A selected skill's instructions do not override host permissions. Document legacy/new session ID dispatch. Do not require invoking every skill; full availability does not mean unconditional use.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
AGENT_WORKFLOW(task):
  session=open(task,project,harness)
  suggestions=recommend(session,task)
  while task needs capabilities:
    choose suggestion OR search full catalog
    descriptor=get(id)
    if skill: read content; follow its required references
    if shared tool: invoke(id,args,session)
    if native: request envelope; use compatible harness action
    preserve execution IDs/evidence
  close(session,outcome,summary)
```

## Acceptance checks and failure behavior

Use a deliberately nonrecommended skill in a contract scenario and prove the workflow can retrieve it. Confirm instructions no longer equate recommendation with authorization or success.

## Dependencies and implementation order

Depends on 11–14. Change only after compatibility surface is implemented and tested.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

