# Research brief — what the NEXUS file audit cannot decide by reading

You are researching primary sources for a remediation plan. Do not audit code; do not read
`/home/roshan/nexus`. Answer the seven questions below from authoritative sources only.

Output: one markdown table per question, plus a final "contested" section. For every row give
`claim | source title | URL | identifier (DOI/arXiv/RFC) | one sentence of the source's own wording`.
Every identifier must be one you actually loaded — if a fetch failed, write `UNVERIFIED` and say so.
A fabricated arXiv id is worse than a gap.

## Q1. Fail-open vs fail-closed defaults in automated execution

Authoritative position on secure defaults for tool execution: when a system cannot verify an
outcome, should it record success, record nothing, or refuse? Look for NIST SP 800-53 (CM-6
Configuration Settings, AU-3/AU-9 audit content and protection), NIST SP 800-160 vol.1 on
assurance, and OWASP ASVS where relevant. Also: least privilege for agent tool invocation.

## Q2. Measurement contamination when a system grades itself

A router evaluates its own routes against a 56-entry table it also indexes. Find primary work on:
evaluation leakage / train-on-test contamination; LLM-as-judge self-preference bias; specification
gaming and reward hacking in RL agents. Prefer peer-reviewed or arXiv with loadable abs.

## Q3. SSRF defence that survives redirects

RFC 6890 (special-purpose address registries), RFC 1918, and the OWASP SSRF Prevention Cheat
Sheet. What does an allowlist have to do per hop when redirects are followed? DNS rebinding.
What does "resolve, check, then connect to the checked address" require in practice?

## Q4. Judging dead code in a dynamically imported Python codebase

Python docs on importlib and dynamic import; coverage.py's documented limits; why grep-based
importer counts miss dynamic imports and entry points. Guidance on safe deletion order.

## Q5. Reciprocal Rank Fusion — which of three copies is canonical

The original RRF paper (Cormack, Clarke, Büttcher, SIGIR 2009) — what is k, what is the formula,
and what does the paper say about tuning it? Then: does RRF require normalised scores?

## Q6. Thompson Sampling for choosing tools — what counts as evidence

Thompson (1933); Chapelle & Li (2011) tutorial; Agrawal & Goyal on regret. Specifically: is a
simulated Bernoulli draw from `times_correct / times_triggered` a valid posterior update, or a
substitute for observation? What happens to the posterior when outcomes are never observed?

## Q7. Honest abstention instead of a fabricated verdict

Selective prediction, conformal prediction, calibration (Guo et al. 2017), and "know when to
abstain" work. A system with no ground truth for most queries: what is the defensible label, and
what does the literature call the failure mode of inventing one?

## Contested

List anything where sources disagree, or where the obvious answer is wrong.
