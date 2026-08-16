#!/usr/bin/env bash
# smoke_e2e.sh — NEXUS end-to-end smoke test (Week1 card 2.4)
# Proves the core loop: health → real completion → feedback event → beliefs moved.
# Usage: ./scripts/smoke_e2e.sh        (targets :8000, per nexus.yaml)
#        NEXUS_BASE=http://127.0.0.1:8080 ./scripts/smoke_e2e.sh   (pre-cutover)
set -euo pipefail
BASE="${NEXUS_BASE:-http://127.0.0.1:8000}"
PY="$(dirname "$0")/../venv/bin/python3"

echo "== NEXUS smoke: $BASE =="

# 1 — liveness
code=$(curl -fsS -o /dev/null -w '%{http_code}' "$BASE/health")
echo "1/4 /health -> $code"

# 2 — real routed completion (verifies provider + API key end-to-end)
# model is required by ChatRequest; Thompson may override it with the sampled winner
resp=$(curl -fsS -X POST "$BASE/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"Reply with exactly: ok"}],"max_tokens":10}')
echo "$resp" | "$PY" -c '
import sys, json
r = json.load(sys.stdin)
model = r["model"]
content = r["choices"][0]["message"]["content"]
assert content.strip(), f"empty completion from {model}"
assert model, "no model in response"
print(f"2/4 chat completion -> model={model} content={content.strip()[:40]!r}")
open("/tmp/nexus_smoke_model", "w").write(model)
'
model=$(cat /tmp/nexus_smoke_model)

# 3 + 4 — feedback event must move the ROUTED MODEL's belief (the loop that IS NEXUS)
"$PY" - "$BASE" "$model" <<'EOF'
import sys, json, urllib.request

base, model = sys.argv[1], sys.argv[2]

def beliefs():
    with urllib.request.urlopen(f"{base}/nexus/beliefs", timeout=10) as r:
        data = json.load(r)
    return {b["model"]: (b["alpha"], b["beta"], b["total_requests"])
            for b in data.get("beliefs", [])}

before = beliefs()
req = urllib.request.Request(
    f"{base}/nexus/event",
    data=json.dumps({
        "ide": "smoke", "event_type": "code_accepted", "model": model,
        "provider": "ollama_cloud", "task_type": "coding", "reward": 1.0,
    }).encode(),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=60) as r:  # 60s: first event may load the embedder on CPU
    assert r.status == 200, f"event POST -> {r.status}"
print("3/4 /nexus/event -> 200")

after = beliefs()
b0, b1 = before.get(model), after.get(model)
assert b1 is not None, f"model {model} has no belief row after feedback — event did not land"
assert b0 != b1, (f"belief for ROUTED model {model} unchanged "
                  f"(alpha/beta/requests {b0} -> {b1}) — learning loop did not close")
print(f"4/4 /nexus/beliefs -> {model} belief moved {b0} -> {b1}")
EOF

echo "SMOKE GREEN"
