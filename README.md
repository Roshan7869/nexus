# NEXUS - Self-Learning LLM Gateway

**One config. All your AI coding agents. Learns from every task.**

---

## Architecture Overview

```
                                    ┌─────────────────────────────────────────────┐
                                    │              NEXUS PROXY                    │
                                    │           localhost:8000                    │
                                    │                                             │
  Claude Code ──MCP──────────────► │  /v1/chat/completions                       │
  Aider ────────OpenAI────────────► │  /v1/models                                 │
  OpenCode ─────OpenAI────────────► │  /nexus/event     ◄── IDE Feedback Events    │
  Codex ────────OpenAI────────────► │  /nexus/learner/stats                      │
  HERMES ───────CLI────────────────► │  /health                                    │
                                    │                                             │
                                    └──────┬──────────────┬──────────────────────┘
                                           │              │
                         Thompson Sampling │              │ Passthrough (no learning)
                         OFF by default    │              │
                                           │              │
                        ┌──────────────────┴──┐   ┌──────┴────────────────┐
                        │    NexusLearner      │   │   Provider Registry   │
                        │  ─────────────────   │   │  ──────────────────  │
                        │  • Beta Beliefs     │   │  • OllamaCloud       │
                        │  • Thompson Sampling│   │  • NVIDIA NIM        │
                        │  • IDE Events       │   │  • LiteLLM           │
                        │  • Benchmarks       │   │                      │
                        │  • SQLite DB        │   └─────────────────────┘
                        └──────────────────────┘

  Signal Flow:
  1. Request arrives → TaskClassifier (coding/reasoning/agentic/general)
  2. routing.enabled=false → passthrough to configured model
  3. routing.enabled=true  → Thompson Sampling picks best model
  4. IDE emits event (code_accepted / test_failed) → NexusLearner updates beliefs
  5. Benchmark runs → NexusLearner updates beliefs across all task types
  6. Next request → updated beliefs → better routing
```

---

## Quick Start

```bash
# 1. Install
pip install -r requirements.txt

# 2. Set API keys
export OLLAMA_CLOUD_API_KEY="..."
export NVIDIA_NIM_API_KEY="..."
export NEXUS_API_KEY="nexus-secret"

# 3. Start (routing OFF by default - just a proxy)
python -m nexus --config nexus.yaml

# 4. Test
curl http://localhost:8000/health

# 5. Enable routing (self-learning)
# Edit nexus.yaml: routing.enabled: true
# Restart
# Now every /nexus/event call updates beliefs
```

---

## Self-Learning System (Thompson Sampling)

### How It Works

Each (model, task_type) pair maintains a **Beta distribution** over quality.

```
Request arrives
     │
     ▼
TaskClassifier: coding / reasoning / agentic / general
     │
     ▼
Thompson Sampling: for each model, sample from Beta(α, β)
     │
     ▼
Pick model with highest sample
     │
     ├─── Request succeeds → IDE emits: code_accepted / test_passed
     │    → reward = +1.0 → Update Beta(α+0.1, β)
     │
     ├─── Request fails  → IDE emits: code_rejected / test_failed
     │    → reward = -1.0 → Update Beta(α, β+0.1)
     │
     └─── Benchmark runs → MMLU/HumanEval scores
          → Update beliefs across all task types
```

### Three Signal Sources

| Signal | Weight | Source | Events |
|--------|--------|--------|--------|
| IDE Feedback | 50% | Claude Code, Aider, OpenCode, etc. | code_accepted, code_rejected, test_passed, test_failed |
| Benchmark | 30% | lm-eval-harness (MMLU, HumanEval, MATH500) | Score per model per benchmark |
| Implicit | 20% | Proxy metrics | Latency, token efficiency, error rate |

### Enabling Routing

```yaml
# nexus.yaml
routing:
  enabled: true           # OFF by default - must explicitly enable
  learn_slowly: true     # Require min_samples before routing changes
  min_samples_before_route: 50
  benchmarks:
    - mmlu
    - humaneval
    - math500
```

### API Endpoints for Learning

```bash
# Emit IDE event
curl -X POST http://localhost:8000/nexus/event \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "code_accepted",
    "model": "deepseek-v4-flash",
    "provider": "ollama_cloud",
    "task_type": "coding",
    "task_description": "Implement user authentication"
  }'

# Get learning stats
curl http://localhost:8000/nexus/learner/stats

# Get current model beliefs
curl http://localhost:8000/nexus/beliefs

# Get recent events
curl http://localhost:8000/nexus/events?limit=20
```

### IDE Event Types

```python
# Positive (reward +1.0 to +0.5)
"task_completed"  # User confirmed task done
"test_passed"     # Tests pass after AI's code
"code_accepted"   # User accepted AI suggestion
"edit_applied"    # Edit was applied to file
"build_success"   # Build passed

# Negative (reward -1.0 to -0.5)
"test_failed"     # Tests fail after AI's code
"code_rejected"  # User rejected AI suggestion
"edit_undone"    # Edit was reverted
"build_failed"   # Build/compile failed
"task_abandoned" # User gave up
"error"          # Request error / timeout
```

---

## IDE Integration

### Claude Code
```bash
export ANTHROPIC_BASE_URL=http://localhost:8000/v1
export ANTHROPIC_API_KEY=nexus-secret
claude-code
```

### Aider
```bash
export OPENAI_API_BASE=http://localhost:8000/v1
export OPENAI_API_KEY=nexus-secret
aider --model deepseek-v4-flash
```

### OpenCode
```bash
export OPENAI_API_BASE=http://localhost:8000/v1
opencode
```

### HERMES
```bash
hermes config set llm.provider nexus
hermes config set nexus.api_base http://localhost:8000/v1
```

---

## File Structure

```
nexus/
├── nexus.yaml                  # ONE config for everything
├── requirements.txt
├── __main__.py
│
├── core/
│   ├── server.py              # FastAPI: /v1/chat/completions, /nexus/event, etc.
│   ├── config.py              # YAML config loader + model registry
│   ├── metrics.py             # MetricsStore: latency, tokens, task type
│   ├── learner.py             # NexusLearner: Thompson Sampling + IDE events
│   ├── router.py              # SlowLearningRouter + BenchmarkRunner
│   └── ide_events.py          # IDEEventCollector + event factory
│
├── providers/
│   └── registry.py           # OllamaCloud, NVIDIA NIM, LiteLLM providers
│
├── approaches/               # OptiLLM-style reasoning (OFF by default)
│   ├── none.py               # Direct passthrough
│   ├── cot_reflection.py     # Chain-of-thought + self-reflection
│   ├── moa.py               # Mixture of Agents
│   ├── mcts.py              # Monte Carlo Tree Search
│   └── bon.py               # Best-of-N
│
├── ide_connector/
│   └── generate.py           # Generate env files + shell aliases
│
└── scripts/
    └── run_benchmark.py       # lm-eval-harness wrapper
```

---

## Research-Backed Design

Based on research from these systems:

| System | Key Technique Used |
|--------|-------------------|
| **Continue Dev** | DataLogger: per-event IDE feedback, trajectory tracking |
| **SWE-agent** | Agent loop with cost/step limits, trajectory serialization |
| **SWE-bench** | Patch-based success: did generated code pass tests? |
| **lm-eval-harness** | MMLU, HumanEval, MATH500 for benchmark ground truth |
| **Thompson Sampling** | Bayesian online learning for model selection |
| **LiteLLM** | Provider abstraction, fallback chains, cost tracking |

---

## Key Behaviors

- **Routing OFF by default**: passthrough until you explicitly enable
- **Conservative start**: uses benchmark scores as prior until enough IDE events accumulate
- **Confidence-gated**: won't route if total samples < min_samples_before_route
- **Blend with benchmarks**: Thompson sample blended with benchmark score (more weight to benchmarks when confidence is low)
- **All models stored**: every request logs to SQLite for analysis
- **IDE-agnostic**: any IDE can emit events via `/nexus/event`