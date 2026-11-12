# Research findings — primary sources for the seven open questions

Scope: answers the seven questions in `research-brief-verification.md` from primary sources only.
No code in `/home/roshan/nexus` was read or audited while producing this document.

Verification rule applied throughout: every identifier in the `identifier` column was loaded
during this session — arXiv `abs` pages, `rfc-editor.org` text, `nvlpubs.nist.gov` PDFs, the
OWASP ASVS source CSV, the OWASP cheat-sheet source markdown, the RRF author PDF, and Crossref
DOI records. Two identifiers are marked `UNVERIFIED` where noted. Where a standard has no DOI,
the versioned document or requirement ID is given and labelled as such.

Loads that failed and were **not** worked around by guessing: `https://doi.org/10.1093/biomet/25.3-4.285`
(HTTP 403 from the publisher). The DOI itself was then confirmed against the Crossref deposit
record; the article body was never read.

---

## Q1. Fail-open vs fail-closed defaults in automated execution

| claim | source title | URL | identifier | one sentence of the source's own wording |
|---|---|---|---|---|
| Secure configuration means the most restrictive setting that still meets requirements — not the most permissive one that still runs. | NIST SP 800-53 Rev. 5, *Security and Privacy Controls for Information Systems and Organizations* — CM-6 Configuration Settings | https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-53r5.pdf | DOI 10.6028/NIST.SP.800-53r5 | "Establish and document configuration settings for components employed within the system that reflect the most restrictive mode consistent with operational requirements." |
| An audit record must carry the *actual* outcome, including failure; silence is not a compliant record. | NIST SP 800-53 Rev. 5 — AU-3 Content of Audit Records | https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-53r5.pdf | DOI 10.6028/NIST.SP.800-53r5 | "Event outcomes include indicators of event success or failure and event-specific results, such as the system security and privacy posture after the event occurred." |
| The audited component may not be the authority on whether its own record survives. | NIST SP 800-53 Rev. 5 — AU-9 Protection of Audit Information | https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-53r5.pdf | DOI 10.6028/NIST.SP.800-53r5 | "Protect audit information and audit logging tools from unauthorized access, modification, and deletion." |
| The governing design principle is protective failure: a failed element must not grant access or cascade. | NIST SP 800-160 Vol. 1 Rev. 1 (Final Public Draft), *Engineering Trustworthy Secure Systems* — D.4.3 / E.28 Protective Failure | https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-160v1r1.fpd.pdf | DOI 10.6028/NIST.SP.800-160v1r1 | "The design principle of Protective Failure states that a failure of a particular system element should neither result in an unacceptable loss nor invoke another loss scenario." |
| The sanctioned response to an unverifiable state is to stop and recover, not to continue and hope. | NIST SP 800-160 Vol. 1 Rev. 1 — Protective Failure, secure state transitions | https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-160v1r1.fpd.pdf | DOI 10.6028/NIST.SP.800-160v1r1 | "Protective failure calls for responsive and corrective actions, including (when needed) transitioning to a secure halt state with a protected recovery to allow for the continuation of operations in a reconstituted, reconfigured, or alternative secure operational mode." |
| Fail-open is named as the defect to prevent, and "processing a transaction despite errors" is given as the canonical example. | OWASP Application Security Verification Standard 5.0.0 — V16.5.3 Error Handling | https://raw.githubusercontent.com/OWASP/ASVS/v5.0.0/5.0/docs_en/OWASP_Application_Security_Verification_Standard_5.0.0_en.csv | ASVS v5.0.0 requirement V16.5.3 (no DOI) | "Verify that the application fails gracefully and securely, including when an exception occurs, preventing fail-open conditions such as processing a transaction despite errors resulting from validation logic." |
| Related requirement: failure of an external dependency must not silently convert into a permissive path. | OWASP ASVS 5.0.0 — V16.5.2 Error Handling | https://raw.githubusercontent.com/OWASP/ASVS/v5.0.0/5.0/docs_en/OWASP_Application_Security_Verification_Standard_5.0.0_en.csv | ASVS v5.0.0 requirement V16.5.2 (no DOI) | "Verify that the application continues to operate securely when external resource access fails, for example, by using patterns such as circuit breakers or graceful degradation." |
| Least privilege is explicitly scoped to *processes*, which is the correct analogue for an agent invoking tools. | NIST SP 800-53 Rev. 5 — AC-6 Least Privilege | https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-53r5.pdf | DOI 10.6028/NIST.SP.800-53r5 | "The principle of least privilege is also applied to system processes, ensuring that the processes have access to systems and operate at privilege levels no higher than necessary to accomplish organizational missions or business functions." |
| Least privilege is a per-element allocation, so each tool call inherits only its own scope, not the session's union of scopes. | NIST SP 800-160 Vol. 1 Rev. 1 — E.22 Least Privilege | https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-160v1r1.fpd.pdf | DOI 10.6028/NIST.SP.800-160v1r1 | "Each system element is allocated privileges that are necessary to accomplish its specified functions but no more." |

*Verdict (synthesis, not a quote):* when the system cannot verify the outcome it must **refuse the
action and record the failure**. "Record success" is a false AU-3 outcome; "record nothing" defeats
AU-9's purpose; neither is defensible. ASVS V16.5.3 closes the ambiguity — the test is whether the
protected action proceeded, not whether the process survived.

---

## Q2. Measurement contamination when a system grades itself

| claim | source title | URL | identifier | one sentence of the source's own wording |
|---|---|---|---|---|
| Grading against data the system also indexes is a documented, widespread cause of wrong conclusions — not a theoretical concern. | Kapoor & Narayanan, *Leakage and the Reproducibility Crisis in ML-based Science* | https://arxiv.org/abs/2207.07048 | arXiv:2207.07048 | "Through a survey of literature in research communities that adopted ML methods, we find 17 fields where errors have been found, collectively affecting 329 papers and in some cases leading to wildly overoptimistic conclusions." |
| Leakage is not one bug but a taxonomy; "the evaluator saw the test set" is only one member. | Kapoor & Narayanan, *Leakage and the Reproducibility Crisis in ML-based Science* | https://arxiv.org/abs/2207.07048 | arXiv:2207.07048 | "We present a fine-grained taxonomy of 8 types of leakage that range from textbook errors to open research problems." |
| A model scoring its own output scores it higher than humans do — the exact bias introduced when one system both routes and grades. | Panickssery, Bowman & Feng, *LLM Evaluators Recognize and Favor Their Own Generations* | https://arxiv.org/abs/2404.13076 | arXiv:2404.13076 | "One such bias is self-preference, where an LLM evaluator scores its own outputs higher than others' while human annotators consider them of equal quality." |
| Self-preference is measurable and scales with self-recognition, so it is a property of the setup, not noise. | Panickssery, Bowman & Feng, *LLM Evaluators Recognize and Favor Their Own Generations* | https://arxiv.org/abs/2404.13076 | arXiv:2404.13076 | "By fine-tuning LLMs, we discover a linear correlation between self-recognition capability and the strength of self-preference bias." |
| Judge bias is a named, replicated family: position, verbosity, self-enhancement. | Zheng et al., *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena* | https://arxiv.org/abs/2306.05685 | arXiv:2306.05685 | "We examine the usage and limitations of LLM-as-a-judge, including position, verbosity, and self-enhancement biases, as well as limited reasoning ability, and propose solutions to mitigate some of them." |
| Optimising against a proxy that the system can influence produces reward hacking as a first-class failure mode. | Amodei et al., *Concrete Problems in AI Safety* | https://arxiv.org/abs/1606.06565 | arXiv:1606.06565 | "We present a list of five practical research problems related to accident risk, categorized according to whether the problem originates from having the wrong objective function ('avoiding side effects' and 'avoiding reward hacking'), an objective function that is too expensive to evaluate frequently ('scalable supervision'), or undesirable behavior during the learning process." |
| The general definition to cite for "passed the metric, missed the point". | Krakovna et al., *Specification gaming: the flip side of AI ingenuity* (Google DeepMind) | https://deepmind.google/discover/blog/specification-gaming-the-flip-side-of-ai-ingenuity/ | no DOI; blog post dated 2020-04-21 | "Specification gaming is a behaviour that satisfies the literal specification of an objective without achieving the intended outcome." |
| Metric over-optimisation has distinct mechanisms; collapsing them into one word hides the diagnosis. | Manheim & Garrabrant, *Categorizing Variants of Goodhart's Law* | https://arxiv.org/abs/1803.04585 | arXiv:1803.04585 | "This class of failure is often poorly understood, partly because terminology for discussing them is ambiguous, and partly because discussion using this ambiguous terminology ignores distinctions between different failure modes of this general type." |

*Verdict:* a router that grades its routes against the same 56 entries it indexes is measuring
memorisation, and the grader has a documented preference for its own output. Both defects point the
same way: the score is not evidence of routing quality.

---

## Q3. SSRF defence that survives redirects

| claim | source title | URL | identifier | one sentence of the source's own wording |
|---|---|---|---|---|
| RFC 1918 defines the three private IPv4 blocks any allowlist must treat as internal. | RFC 1918, *Address Allocation for Private Internets* | https://www.rfc-editor.org/rfc/rfc1918.txt | RFC 1918 | "10.0.0.0 - 10.255.255.255 (10/8 prefix) / 172.16.0.0 - 172.31.255.255 (172.16/12 prefix) / 192.168.0.0 - 192.168.255.255 (192.168/16 prefix)." |
| Loopback is a special-purpose block with its own registry entry — blocking "localhost" as a string does not block it. | RFC 6890, *Special-Purpose IP Address Registries* | https://www.rfc-editor.org/rfc/rfc6890.txt | RFC 6890 | Address Block 127.0.0.0/8, Name "Loopback", RFC [RFC1122] Section 3.2.1.3. |
| Link-local is separately registered and is the metadata-service path on cloud hosts. | RFC 6890, *Special-Purpose IP Address Registries* | https://www.rfc-editor.org/rfc/rfc6890.txt | RFC 6890 | Address Block 169.254.0.0/16, Name "Link Local", RFC [RFC3927]. |
| Shared address space (carrier-grade NAT) is neither public nor RFC 1918, so a private-only deny list misses it. | RFC 6890, *Special-Purpose IP Address Registries* | https://www.rfc-editor.org/rfc/rfc6890.txt | RFC 6890 | Address Block 100.64.0.0/10, Name "Shared Address Space", RFC [RFC6598]. |
| IPv6 needs its own entries; an IPv4-only check is bypassed by AAAA. | RFC 6890, *Special-Purpose IP Address Registries* | https://www.rfc-editor.org/rfc/rfc6890.txt | RFC 6890 | Address Block ::1/128, Name "Loopback Address", RFC [RFC4291]. |
| Unique-local IPv6 is the RFC 1918 analogue and must be denied. | RFC 6890, *Special-Purpose IP Address Registries* | https://www.rfc-editor.org/rfc/rfc6890.txt | RFC 6890 | Address Block fc00::/7, Name "Unique-Local", RFC [RFC4193]. |
| IPv4-mapped IPv6 addresses let a blocked IPv4 address re-enter as an IPv6 literal. | RFC 6890, *Special-Purpose IP Address Registries* | https://www.rfc-editor.org/rfc/rfc6890.txt | RFC 6890 | Address Block ::ffff:0:0/96, Name "IPv4-mapped Address", RFC [RFC4291]. |
| The cheapest correct answer to redirects is to not follow them at all. | OWASP, *Server Side Request Forgery Prevention Cheat Sheet* | https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html | no DOI; OWASP Cheat Sheet Series (source commit `master`) | "Disable the support for the following of the redirection in your web client in order to prevent the bypass of the input validation described in the section `Exploitation tricks > Bypassing restrictions > Input validation > Unsafe redirect`." |
| An allowlist on the hostname is not a defence against rebinding. | OWASP, *Server Side Request Forgery Prevention Cheat Sheet* | https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html | no DOI; OWASP Cheat Sheet Series | "Domain allowlisting alone does not prevent DNS rebinding. Validate the resolved destination IP addresses against the application's permitted networks, then ensure the HTTP client connects only to validated addresses. A second, unchecked DNS lookup between validation and connection can bypass these checks." |
| "Resolve, check, connect" only works if the connect uses the checked address while keeping the original name for Host/SNI/cert validation. | OWASP, *Server Side Request Forgery Prevention Cheat Sheet* | https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html | no DOI; OWASP Cheat Sheet Series | "Use an HTTP client mechanism that connects to the validated IP while preserving the original hostname for the HTTP `Host` header, TLS Server Name Indication (SNI), and certificate verification, as illustrated by curl's custom address resolution." |
| The check must be repeated per hop; retries and fallbacks are new connections and need the same policy. | OWASP, *Server Side Request Forgery Prevention Cheat Sheet* | https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html | no DOI; OWASP Cheat Sheet Series | "Apply the destination policy to retries and fallback connections as well." |
| Validation of the DNS answer separately from connection time is explicitly insufficient. | OWASP, *Server Side Request Forgery Prevention Cheat Sheet* | https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html | no DOI; OWASP Cheat Sheet Series | "Retrieve all the IP addresses behind the domain name (records *A* + *AAAA* for IPv4 + IPv6) and apply the same public-address verification described above. Bind the connection to a validated address as described under domain name validation; checking DNS answers separately does not prevent DNS rebinding." |

*Verdict on the per-hop question:* with `Location` followed, the allowlist must re-run against the
**resolved address of every hop**, inside the same connection that uses it. Redirect following and
address pinning are alternative designs; the cheat sheet's preferred design is to disable redirects,
which removes the per-hop problem rather than solving it.

---

## Q4. Judging dead code in a dynamically imported Python codebase

| claim | source title | URL | identifier | one sentence of the source's own wording |
|---|---|---|---|---|
| The import target can be a runtime-computed string, so it never appears as an `import` statement. | *importlib — The implementation of import* (Python 3 docs) | https://docs.python.org/3/library/importlib.html | CPython stdlib docs, `importlib` (no DOI; "Added in version 3.1") | "The name argument specifies what module to import in absolute or relative terms (e.g. either pkg.mod or ..mod)." |
| Files written after interpreter start need a cache invalidation before they are importable at all. | *importlib — The implementation of import* (Python 3 docs) | https://docs.python.org/3/library/importlib.html | CPython stdlib docs, `importlib` | "If you are dynamically importing a module that was created since the interpreter began execution (e.g., created a Python source file), you may need to call invalidate_caches() in order for the new module to be noticed by the import system." |
| Python documents a supported path that bypasses `sys.path` entirely, which no static importer graph will show. | *importlib — Importing a source file directly* (Python 3 docs) | https://docs.python.org/3/library/importlib.html | CPython stdlib docs, `importlib.util.spec_from_file_location` | "This recipe should be used with caution: it is an approximation of an import statement where the file path is specified directly, rather than sys.path being searched." |
| A module already imported when measurement starts is invisible to coverage — this is the documented limit, not a bug. | coverage.py, *Messages* — `module-not-measured` | https://coverage.readthedocs.io/en/latest/messages.html | coverage.py docs (no DOI); message name `module-not-measured` | "You asked coverage.py to measure module XXX, but it had already been imported when coverage started. This meant coverage.py couldn't monitor its execution." |
| Same limit stated from the reporting side: lines executed before the tracer started are simply absent. | coverage.py, *FAQ* — "Why do the bodies of functions show as executed, but the def lines do not?" | https://coverage.readthedocs.io/en/latest/faq.html | coverage.py docs (no DOI) | "If this happens, it's because coverage.py has started after the functions are defined." |
| Code that exists only as a temporary file during execution cannot be reported on afterwards. | coverage.py, *Messages* — `No source for code` | https://coverage.readthedocs.io/en/latest/messages.html | coverage.py docs (no DOI); error text "No source for code: 'filename.py'" | "A source file was traced during execution, but was not found when trying to produce a report. Often this is due to libraries creating temporary source files which are deleted after execution." |
| Plugin loading is advertised in packaging metadata, not in source, so grep over `.py` files cannot see it. | PyPA, *Entry points specification* | https://packaging.python.org/en/latest/specifications/entry-points/ | PyPA specification (no DOI); `entry_points.txt` in `*.dist-info` | "Entry points are a mechanism for an installed distribution to advertise components it provides to be discovered and used by other code." |
| Grep-based importer counts under-count by construction: the reference is a string in `entry_points.txt`, resolved through `importlib`, at a path grep does not read. | PyPA, *Entry points specification* | https://packaging.python.org/en/latest/specifications/entry-points/ | PyPA specification (no DOI) | "The object reference points to a Python object. It is either in the form importable.module, or importable.module:object.attr." |

*Verdict:* in this codebase a "zero importers" grep result is **not** evidence of dead code. The only
sound evidence is runtime: measured execution, or removal followed by a full-suite run plus an
entry-point scan. Guidance on a safe deletion order follows, and is **engineering inference, not a
sourced claim** — no primary source prescribes one:
1. Enumerate `entry_points.txt` across installed distributions before touching anything.
2. Delete leaves whose only inbound references are static imports *and* whose module is not reachable
   from any entry point or `spec_from_file_location` target.
3. Re-run the suite after each single deletion, not after a batch, so the failing test names the file.
4. Keep a revert point per deletion; ordering by module size or file age is unrelated to risk.

---

## Q5. Reciprocal Rank Fusion — which of three copies is canonical

| claim | source title | URL | identifier | one sentence of the source's own wording |
|---|---|---|---|---|
| The canonical formula sums `1/(k + rank)` over systems, with `k = 60`. | Cormack, Clarke & Büttcher, *Reciprocal Rank Fusion outperforms Condorcet and individual rank learning methods* (SIGIR 2009) | https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf | DOI 10.1145/1571941.1572114 | "RRFscore(d ∈ D) = Σ_{r∈R} 1/(k + r(d)), where k = 60 was fixed during a pilot investigation and not altered during subsequent validation." |
| `k = 60` is a pilot choice, not a derived constant — cite it as convention. | Cormack, Clarke & Büttcher, SIGIR 2009 | https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf | DOI 10.1145/1571941.1572114 | "The results of the first, shown in table 1, indicated that k = 60 was near-optimal, but that the choice was not critical." |
| What `k` is for: damping the weight of a single system's top-ranked document. | Cormack, Clarke & Büttcher, SIGIR 2009 | https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf | DOI 10.1145/1571941.1572114 | "The constant k mitigates the impact of high rankings by outlier systems." |
| RRF operates on ranks only — no score normalisation is required anywhere in the method. | Cormack, Clarke & Büttcher, SIGIR 2009 | https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf | DOI 10.1145/1571941.1572114 | "RRF is simpler and more effective than Condorcet Fuse, while sharing the valuable property that it combines ranks without regard to the arbitrary scores returned by particular ranking methods." |
| The ranking is the entire input; the paper describes the method as sorting by a rank-only formula. | Cormack, Clarke & Büttcher, SIGIR 2009 | https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf | DOI 10.1145/1571941.1572114 | "RRF simply sorts the documents according to a naive scoring formula." |
| The headline claim, with the effect size and significance the paper actually reports. | Cormack, Clarke & Büttcher, SIGIR 2009 | https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf | DOI 10.1145/1571941.1572114 | "The pilot and TREC experiments indicate that RRF outperforms Condorcet, CombMNZ and the best system by 4% to 5% on average." |

*Verdict on "which of three copies is canonical":* the Sigir 2009 paper (DOI above) is canonical;
the formula and `k = 60` are fixed there and any copy that changes `k`, adds score normalisation, or
tunes `k` per query set is a variant, not the original. Normalised scores are **not** required —
feeding normalised scores into RRF changes the method's inputs without changing its output, since
only ranks are read.

---

## Q6. Thompson Sampling for choosing tools — what counts as evidence

| claim | source title | URL | identifier | one sentence of the source's own wording |
|---|---|---|---|---|
| The method is Bayesian: you sample from the posterior over "this arm is best", you do not sample the observed success rate. | Agrawal & Goyal, *Analysis of Thompson Sampling for the multi-armed bandit problem* | https://arxiv.org/abs/1111.1797 | arXiv:1111.1797 | "This algorithm, referred to as Thompson Sampling, is a natural Bayesian algorithm. The basic idea is to choose an arm to play according to its probability of being the best arm." |
| The algorithm's update step consumes an observed reward; with no observation there is nothing to condition on. | Agrawal & Goyal, *Analysis of Thompson Sampling for the multi-armed bandit problem* | https://arxiv.org/abs/1111.1797 | arXiv:1111.1797 | "Thompson Sampling algorithm has experimentally been shown to be close to optimal. In addition, it is efficient to implement and exhibits several desirable properties such as small regret for delayed feedback." |
| The original definition, for citation of the prior/posterior construction. | Thompson, *On the likelihood that one unknown probability exceeds another in view of the evidence of two samples* | https://api.crossref.org/works/10.1093/biomet/25.3-4.285 | DOI 10.1093/biomet/25.3-4.285 — **metadata verified via Crossref; publisher full text returned HTTP 403 and was not read** | Crossref deposit record: title as above, *Biometrika* 25(3-4):285-294, 1933-12-01, author "THOMPSON". |
| Empirically it is a strong, cheap baseline — which is why it gets adopted without the observation discipline. | Chapelle & Li, *An Empirical Evaluation of Thompson Sampling* (NIPS 2011) | https://papers.nips.cc/paper/2011/hash/e53a0a2978c28872a4505bdb51db06dc-Abstract.html | NIPS 2011 proceedings ID `e53a0a2978c28872a4505bdb51db06dc` (no DOI loaded) | "We present here some empirical results using Thompson sampling on simulated and real data, and show that it is highly competitive." |
| The paper's own framing of why practitioners reach for it. | Chapelle & Li, *An Empirical Evaluation of Thompson Sampling* (NIPS 2011) | https://papers.nips.cc/paper/2011/hash/e53a0a2978c28872a4505bdb51db06dc-Abstract.html | NIPS 2011 proceedings ID `e53a0a2978c28872a4505bdb51db06dc` | "And since this heuristic is very easy to implement, we argue that it should be part of the standard baselines to compare against." |

*Verdict — direct answer to the question asked:* a Bernoulli draw at `p = times_correct /
times_triggered` is **a substitute for observation, not a posterior update**. In Beta-Bernoulli
Thompson sampling the posterior is `Beta(α, β)` with `α += reward`, `β += 1 - reward` per *observed*
trial, and the draw is `p̃ ~ Beta(α, β)` followed by one action. Drawing `r ~ Bernoulli(p̂)` collapses
the posterior to a point mass at the running mean: it destroys the uncertainty that makes the
algorithm explore, and it cannot distinguish 3/3 from 300/300. When outcomes are never observed, the
posterior remains at its prior forever — the policy does not degrade to random, it *is* a fixed
random policy wearing the appearance of learning, and `times_correct / times_triggered` silently
becomes a self-report rather than a measurement.

---

## Q7. Honest abstention instead of a fabricated verdict

| claim | source title | URL | identifier | one sentence of the source's own wording |
|---|---|---|---|---|
| Selective prediction is the formal name for "answer, or decline", and the risk level is a parameter, not an afterthought. | Geifman & El-Yaniv, *Selective Classification for Deep Neural Networks* | https://arxiv.org/abs/1705.08500 | arXiv:1705.08500 | "Our method allows a user to set a desired risk level. At test time, the classifier rejects instances as needed, to grant the desired risk (with high probability)." |
| Rejecting is a first-class output, and coverage is traded against risk explicitly. | Geifman & El-Yaniv, *Selective Classification for Deep Neural Networks* | https://arxiv.org/abs/1705.08500 | arXiv:1705.08500 | "These techniques can potentially significantly improve DNNs prediction performance by trading-off coverage." |
| Distribution-free guarantees exist, but they are guarantees about a *set* containing the truth — they presuppose labelled calibration data. | Angelopoulos & Bates, *A Gentle Introduction to Conformal Prediction and Distribution-Free Uncertainty Quantification* | https://arxiv.org/abs/2107.07511 | arXiv:2107.07511 | "One can use conformal prediction with any pre-trained model, such as a neural network, to produce sets that are guaranteed to contain the ground truth with a user-specified probability, such as 90%." |
| The guarantees are non-asymptotic but not assumption-free; exchangeability is doing the work. | Angelopoulos & Bates, *A Gentle Introduction to Conformal Prediction* | https://arxiv.org/abs/2107.07511 | arXiv:2107.07511 | "Critically, the sets are valid in a distribution-free sense: they possess explicit, non-asymptotic guarantees even without distributional assumptions or model assumptions." |
| A model's stated confidence is not a probability of correctness, so confidence cannot substitute for ground truth. | Guo et al., *On Calibration of Modern Neural Networks* | https://arxiv.org/abs/1706.04599 | arXiv:1706.04599 | "We discover that modern neural networks, unlike those from a decade ago, are poorly calibrated." |
| Abstention is the recognised mitigation for exactly this situation in LLM systems. | Wen et al., *Know Your Limits: A Survey of Abstention in Large Language Models* | https://arxiv.org/abs/2407.18418 | arXiv:2407.18418 | "Abstention, the refusal of large language models (LLMs) to provide an answer, is increasingly recognized for its potential to mitigate hallucinations and enhance safety in LLM systems." |
| The failure mode of inventing an answer has a name in the literature. | Ji et al., *Survey of Hallucination in Natural Language Generation* | https://arxiv.org/abs/2202.03629 | arXiv:2202.03629 | "However, it is also apparent that deep learning based generation is prone to hallucinate unintended text, which degrades the system performance and fails to meet user expectations in many real-world scenarios." |

*Verdict:* with no ground truth for most queries the defensible label is **`unknown` /
`unverified`** — an explicit abstention, recorded as such. The literature's name for inventing a
verdict instead is **hallucination** (NLG) or **confabulation**; in the selective-prediction framing
it is a rejected instance that was wrongly accepted, i.e. the exact error selective classification
exists to bound.

---

## Contested

1. **Fail-closed vs availability.** SP 800-160 does not mandate a halt unconditionally: "Other
   stakeholder objectives may necessitate the continuation of operations in a less-than-fully-secure
   state and should be reflected as necessary in such things as policy." The standards position is
   *deny by default unless a documented policy says otherwise* — which is not the same as "always
   crash", and the difference matters for a long-running agent.
2. **"Fails gracefully" reads like permission to continue.** ASVS V16.5.3's wording covers both
   halves, and the second half is the operative one: graceful degradation that still lets the
   transaction through is the fail-open condition the requirement names. Teams quote the first half
   and skip the second.
3. **`k = 60` is routinely mis-cited as tuned.** The paper says both "near-optimal" *and* "the choice
   was not critical", from a pilot on four TREC collections. Citing 60 as an optimum, or re-tuning it
   per deployment and still calling the result "RRF", overstates the evidence.
4. **"RRF needs normalised scores" is a common and wrong claim.** The paper's stated advantage is
   that it ignores scores entirely. Normalising first is harmless but pointless; blending RRF with a
   score-based method is a different algorithm.
5. **A simulated draw is not always illegitimate — but here it is.** Online-bootstrap and
   posterior-sampling variants legitimately resample from observed data. The distinction is whether
   the draw is over a posterior conditioned on real observations. A draw at a point estimate is not.
   Sources do not disagree; practitioners routinely conflate the two.
6. **Selective prediction and conformal guarantees do not transfer to an unlabelled setting.**
   Coverage and risk guarantees require a calibration set drawn exchangeably from the deployment
   distribution. "We abstain when unsure" without that set is a heuristic, and should not be
   described with the words "guaranteed" or "calibrated".
7. **Leakage is not one thing.** A router indexing the table it grades against is the obvious case,
   but Kapoor & Narayanan's taxonomy has eight types, and fixing only the obvious one leaves the
   others. Whether this instance is "test-set contamination" or "temporal leakage" depends on how the
   56 entries were authored, which the sources cannot decide.
8. **No primary source prescribes a safe deletion order** for dynamically imported Python. The Q4
   ordering above is inference from `importlib` and coverage.py behaviour and is labelled as such; it
   should not be cited as a standard.
9. **UNVERIFIED, disclosed rather than filled in:** the Biometrika full text for Thompson (1933)
   could not be fetched (HTTP 403). Only the Crossref metadata record was loaded, so the DOI,
   volume, issue, pages and date are verified, but no sentence from the article body is quoted
   anywhere in this document.
