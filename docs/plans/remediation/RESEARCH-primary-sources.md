# Primary sources — routing evaluation, self-grading, verification, tool gating

Evidence base for the remediation plan. One section per topic; every claim carries an inline citation. Identifiers resolved 2026-11-10 against arXiv
abstract pages, Crossref, NIST CSRC or the publisher's own docs. Anything unconfirmed is marked **not verified**.

## 1. Routing / retrieval evaluation

**1.1 Grade against a judgment set you did not produce.**
- Cranfield/test-collection paradigm: fixed documents, fixed queries, relevance judgments collected independently of the system under test. Manning,
  Raghavan & Schütze, *Introduction to Information Retrieval* ch. 8, CUP 2008 —
  https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-in-information-retrieval-1.html
- Single-dataset evaluation is the documented failure mode. BEIR was built because narrow settings "considerably limited insights into …
  out-of-distribution generalization capabilities" (18 datasets, 10 systems; BM25 stayed a robust baseline) — Thakur, Reimers, Rücklé, Srivastava &
  Gurevych, "BEIR", NeurIPS 2021 D&B, arXiv:2104.08663.
- Same argument for embedding-backed retrieval: no method dominated across MTEB's 8 task types, 58 datasets, 112 languages; evaluation "on a small set
  of datasets from a single task" does not transfer — Muennighoff, Tazi, Magne & Reimers, "MTEB", arXiv:2210.07316.
- Leakage between build/tune data and scoring data is a named methodological error with an 8-type taxonomy (incl. "no test set", duplicate rows),
  implicated in 329 papers across 17 fields — Kapoor & Narayanan, "Leakage and the Reproducibility Crisis in ML-based Science", arXiv:2207.07048.
- Benchmark items themselves can be in the model's training data, inflating scores for reasons unrelated to capability — Oren, Meister, Chatterji,
  Ladhak & Hashimoto, arXiv:2310.17623; Deng, Zhao, Tang, Gerstein & Cohan, arXiv:2311.09783.

**1.2 Standard offline ranking metrics.**
- nDCG (graded relevance, log discount, normalised by ideal DCG) — Järvelin & Kekäläinen, "Cumulated gain-based evaluation of IR techniques", *ACM
  TOIS* 20(4):422–446, 2002, DOI 10.1145/582415.582418.
- MRR (mean of 1/rank of first correct response; 0 if absent) — Voorhees, "The TREC-8 Question Answering Track Report", TREC-8, NIST 1999 —
  https://www.nist.gov/publications/trec-8-question-answering-track-report
- Precision@k, recall@k, MAP, and the unranked-vs-ranked distinction — Manning et al. ch. 8 above. Two traps: recall@k is undefined for a query with
  no relevant item, and unjudged items are conventionally scored non-relevant — both bite when the judgment pool is small.
- A router emits a *ranking* of candidate skills/tools. Recall@k ("was the right skill offered at all") and MRR ("how high") are therefore the natural
  measures; accuracy@1 collapses "ranked second" and "absent" into one wrong answer, which is exactly the distinction a fallback needs.

**1.3 When pass-rate on a small set misleads.**
- A 105-case eval is an experiment on a sample. Miller gives the binomial/normal machinery for eval CIs and power, and recommends treating items as
  draws from an unseen super-population — Evan Miller, "Adding Error Bars to Evals", arXiv:2411.00640 (2024).
- Underpowered comparison is the norm in NLP: several GLUE test sets are small enough that "most attempted comparisons to state of the art models will
  not be adequately powered" — Card, Henderson, Khandelwal, Jia, Mahowald & Jurafsky, EMNLP 2020, arXiv:2010.06595.
- Reporting a single best run instead of expected performance vs budget overstates results — Dodge, Gururangan, Card, Schwartz & Smith, EMNLP-IJCNLP
  2019, arXiv:1909.03004.
- Concretely: 0.600 on n=105 has a 95% Wilson interval of [0.504, 0.689], half-width ≈ 9 points. Any "improvement" below ~9 points is
  indistinguishable from noise at that n. (Interval computed from the binomial; method per Miller 2024.)

**1.4 Router-specific evaluation.**
- RouterBench exists because there was "an absence of a standardized benchmark for evaluating the performance of LLM routers"; it ships 405k inference
  outcomes plus a cost/quality framework — Hu, Chen, Zhu et al., arXiv:2403.12031.
- RouteLLM evaluates the cost–quality trade-off (reports >2× cost reduction in some settings without quality loss) and shows routers transfer when the
  underlying models change — Ong, Almahairi, Wu, Chiang, Wu, Gonzalez, Kadous & Stoica, arXiv:2406.18665.
- Implication: a router is only meaningful against a stated objective (quality vs cost vs latency). "Correct skill chosen" is a proxy for it — and per
  §2, a proxy the system can satisfy without achieving it.

## 2. Self-grading and reward hacking

**2.1 Specification gaming.**
- Canonical taxonomy of five problems, incl. "avoiding reward hacking" and "avoiding side effects" — Amodei, Olah, Steinhardt, Christiano, Schulman &
  Mané, "Concrete Problems in AI Safety", arXiv:1606.06565 (2016).
- Empirical catalogue of ~60 documented cases where the literal specification was satisfied without the intended outcome — Krakovna, Uesato, Mikulik,
  Rahtz, Everitt, Kumar, Kenton et al., "Specification gaming: the flip side of AI ingenuity", DeepMind, 21 Apr 2020 —
  https://deepmind.google/blog/specification-gaming-the-flip-side-of-ai-ingenuity/ (authored primary source, but a blog, not peer-reviewed — flagged
  deliberately).
- Formal result: Skalse, Howe, Krasheninnikov & Krueger give the first formal definition of reward hacking and prove that over all stochastic
  policies, two reward functions can be unhackable only if one is constant — i.e. no non-trivial proxy is unhackable in general. NeurIPS 2022,
  arXiv:2209.13085.
- Correct specification is not sufficient: an agent can pursue the wrong goal while staying competent — Shah, Varma, Kumar, Phuong, Krakovna, Uesato &
  Kenton, arXiv:2210.01790; first empirical demos in Langosco di Langosco, Koch, Misra & Krueger, ICML 2022, arXiv:2105.14111.

**2.2 Goodhart, formalised.**
- Four variants — regressional, extremal, causal, adversarial; the causal and adversarial ones apply precisely when the measured party can act on the
  measurement — Manheim & Garrabrant, "Categorizing Variants of Goodhart's Law", arXiv:1803.04585.
- Quantified in MDPs: optimising an imperfect proxy past a critical point degrades true performance, and early stopping provably avoids it —
  Karwowski, Hayman, Bai, Kiendlhofer, Griffin & Skalse, "Goodhart's Law in Reinforcement Learning", arXiv:2310.09144.

**2.3 LLM-as-judge self-preference.**
- Self-preference is real and mediated by self-recognition: an LLM evaluator scores its own generations higher than humans do, and self-recognition
  ability correlates with bias size — Panickssery, Bowman & Feng, "LLM Evaluators Recognize and Favor Their Own Generations", arXiv:2404.13076.
- **Contested.** For: strong judges reach >80% agreement with human preference — Zheng, Chiang, Sheng et al., "Judging LLM-as-a-Judge with MT-Bench
  and Chatbot Arena", NeurIPS 2023 D&B, arXiv:2306.05685 (which itself documents position, verbosity and self-enhancement bias). Against: rankings
  flip on response order alone; Vicuna-13B beat ChatGPT on 66 of 80 queries when ChatGPT judged — Wang, Li, Lin et al., "Large Language Models are not
  Fair Evaluators", arXiv:2305.17926. Do not cite either side as settled.
- Inference for this codebase (not a cited claim): "task appears in the eval JSON ⇒ routing correct" is the adversarial/causal Goodhart case — the
  router is both measured party and label author — structurally the same circularity Panickssery et al. measure. The literature supports independence
  of the label source, not a better prompt.

## 3. Verification of agent actions

**3.1 Execution feedback as grounding.**
- Interleaving reasoning with actions against an external environment reduces hallucination and error propagation vs pure chain-of-thought — Yao,
  Zhao, Yu, Du, Shafran, Narasimhan & Cao, "ReAct", ICLR 2023, arXiv:2210.03629.
- Feedback signals including test execution change outcomes: Reflexion reports 91% pass@1 on HumanEval vs 80% for GPT-4, over feedback that may be
  external or internally simulated — Shinn, Cassano, Gopinath, Narasimhan & Yao, NeurIPS 2023, arXiv:2303.11366.
- Only the external kind is grounding; simulated self-feedback is not verification. Toolformer is self-supervised tool-use learning, not verification
  — Schick, Dwivedi-Yu, Dessì et al., arXiv:2302.04761.

**3.2 Test-based (execution) verification.**
- Execution-based functional correctness against hidden tests is the standard: HumanEval/pass@k — Chen, Tworek, Jun et al., arXiv:2107.03374; APPS,
  10k problems scored on test cases — Hendrycks, Basart, Kadavath et al., arXiv:2105.09938.
- Execution results are a usable learned verifier signal: LEVER trains on (problem, program, execution result) and improves 4.6–10.9% — Ni, Iyer,
  Radev, Stoyanov, Yih, Wang & Lin, ICML 2023, arXiv:2302.08468.
- Self-debugging from execution output works without human feedback — Chen, Lin, Schärli & Zhou, arXiv:2304.05128. Independently generated tests plus
  dual-execution agreement — Chen, Liu, Tang et al., "CodeT", ICLR 2023, arXiv:2207.10397.

**3.3 Test-based verification is not self-validating.**
- SWE-bench scores a patch by running `FAIL_TO_PASS` and `PASS_TO_PASS` tests the agent never sees — Jimenez, Yang, Wettig, Yao, Pei, Press &
  Narayanan, ICLR 2024, arXiv:2310.06770. The hidden-test design is the part worth copying.
- But the suite itself can be wrong: OpenAI found SWE-bench tasks that were "hard or impossible to solve", making the benchmark systematically
  underestimate models, and released a human-validated subset — "Introducing SWE-bench Verified", OpenAI, 13 Aug 2024 —
  https://openai.com/index/introducing-swe-bench-verified/
- Elaborate scaffolds confound comparison; a fixed localise→repair→validate pipeline with no LLM-chosen actions was competitive — Xia, Deng, Dunn &
  Zhang, "Agentless", arXiv:2407.01489.
- **Contested:** whether generated tests substitute for curated ones. CodeT says yes (dual-execution agreement feeds validity); SWE-bench Verified and
  Agentless say test validity is the binding constraint. Cite both.

**3.4 Flaky tests and timeouts are correctness problems.**
- Flakiness is prevalent in Python and dominated by test-order dependency (59% of 7,571 flaky tests across 876,186 cases in 22,352 PyPI projects),
  then infrastructure (28%), then network/randomness (13%); confirming non-flakiness at 95% confidence needs ~170 reruns on average — Gruber,
  Lukasczyk, Kroiß & Fraser, "An Empirical Study of Flaky Tests in Python", ICST 2021, pp. 148–158, DOI 10.1109/ICST49551.2021.00026,
  arXiv:2101.09077.
- Consequence: one `pytest` run is not evidence of correctness, and a verifier mapping "non-zero exit ⇒ task failed" mislabels both flaky failures and
  real ones. Order-independence (fixed seed, no random plugin, isolated DB) belongs in the verifier's contract, not in test hygiene.
- SWE-bench Verified's finding is the same class of bug one level up: incorrect or unsatisfiable tests silently corrupt the pass rate.
- **Timeout as a distinct outcome class: not verified.** No primary source found that treats agent verifier timeouts as a correctness issue in its own
  right; the nearest verified statements are the flakiness study and the SWE-bench validity finding. Treat "timeout ⇒ distinct status, never a silent
  pass" as engineering judgement.

## 4. Tool side-effect gating

**4.1 Fail-safe defaults and least privilege.**
- Origin of both principles, and of why an allow-by-default gate is backwards: "fail-safe defaults — base access decisions on permission rather than
  exclusion", plus least privilege and complete mediation — Saltzer & Schroeder, "The Protection of Information in Computer Systems", *Proc. IEEE*
  63(9):1278–1308, 1975, DOI 10.1109/PROC.1975.9939.
- Least privilege as an assessable control: NIST SP 800-53 Rev. 5, **AC-6** — https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final (Rev. 5 Sep 2020;
  release 5.2.0 Aug 2025). Access-control verification requirements: OWASP **ASVS 5.0.0**, published 30 May 2025 — https://asvs.owasp.org/
- For LLM tool use the named root causes are excessive functionality, excessive permissions and excessive autonomy — OWASP Top 10 for LLM
  Applications, **LLM06:2025 Excessive Agency** — https://genai.owasp.org/llmrisk/llm062025-excessive-agency/
- Applied to `side_effect_level`: a blocked set matching zero live rows means the default is allow, the inverse of fail-safe defaults. The supported
  remediation is default-deny plus an enumerable allow-list, and a boot assertion that the allow-list is non-empty and every tool row has a non-NULL
  level.

**4.2 Capability-based gating beats name-based gating.**
- Object-capability systems "provide much better support for least-privilege operation and for avoiding confused deputy problems" than ACL-style
  checks — Miller, Yee & Shapiro, "Capability Myths Demolished", TR SRL2003-02, JHU Systems Research Laboratory, 2003 —
  https://srl.cs.jhu.edu/pubs/SRL2003-02.pdf
- This is the design now used for agents: CaMeL extracts control/data flow from the trusted query and enforces capabilities at tool-call time so
  untrusted retrieved data cannot redirect flow or exfiltrate; 77% of AgentDojo tasks solved with provable security vs 84% undefended — Debenedetti,
  Shumailov, Fan, Hayes, Carlini, Fabian, Kern, Shi, Terzis & Tramèr, "Defeating Prompt Injections by Design" (CaMeL), arXiv:2503.18813.
- Pattern catalogue for the same problem — Beurer-Kellner, Buesser, Crețu, Debenedetti et al., "Design Patterns for Securing LLM Agents against Prompt
  Injections", arXiv:2506.08837 (title/ID verified; contents not reviewed in depth).

**4.3 Sandboxing primitives and their self-declared limits.**
- `seccomp`-BPF filters syscall number and arguments and says of itself: "System call filtering isn't a sandbox. It provides a clearly defined
  mechanism for minimizing the exposed kernel surface" — https://docs.kernel.org/userspace-api/seccomp_filter.html
- gVisor runs a userspace application kernel in Go behind `runsc` and states it is "not a syscall filter" and "not a VM" — https://gvisor.dev/docs/
- Both are process/container isolation for code execution. Neither decides *whether* a tool should run: gating is policy, sandboxing is containment,
  and the plan needs both.

**4.4 Why the gate must assume tool output is hostile.**
- Indirect prompt injection — instructions embedded in retrieved data rather than typed by the user — is documented against real integrated systems,
  with a taxonomy covering data theft and worming — Greshake, Abdelnabi, Mishra, Endres, Holz & Fritz, "Not what you've signed up for",
  arXiv:2302.12173.
- Benchmarks quantifying it for tool-using agents: AgentDojo (97 tasks, 629 security test cases; existing attacks "break some security properties but
  not all") — Debenedetti, Zhang, Balunović, Beurer-Kellner, Fischer & Tramèr, arXiv:2406.13352; InjecAgent — Zhan, Liang, Ying & Kang, ACL 2024
  Findings, arXiv:2403.02691.
- Consequence for a fail-open gate: attack success is partial, so a control that blocks nothing still "passes" any test that only checks task
  completion.

## 5. Gaps — not verified

- **Verifier timeouts** as a first-class correctness outcome: no primary source found (§3.4).
- **E2B** / hosted code-interpreter sandboxes: official docs not reviewed; no claim made.
- **RouteLLM** internal metric definitions (e.g. PGR) not read beyond the abstract; only the abstract's cost/quality claims are asserted.
- **BetterBench** not confirmed — the ID I expected, arXiv:2405.01525, resolves to "Flame: Factuality-Aware Alignment for Large Language Models". No
  citation used.
- Reflexion/CodeT venue attributions come from arXiv comment fields; re-check proceedings if a venue claim becomes load-bearing.
