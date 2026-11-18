import subprocess, json
from collections import defaultdict

Q = ['node', '.gitnexus/run.cjs', 'cypher', '-r', 'nexus']

def q(query, timeout=180):
    r = subprocess.run(Q + [query], capture_output=True, text=True, timeout=timeout)
    try:
        d = json.loads(r.stdout.strip())
    except Exception:
        return None
    md = d.get('markdown', '')
    if md:
        md = md.replace('\\n', '\n')
    rows = []
    for line in md.split('\n')[2:]:
        parts = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(parts) == 4:
            rows.append(parts)
    return rows

BASE = "MATCH (n)-[r:CodeRelation]->(p:Process) WHERE r.type='STEP_IN_PROCESS' RETURN p.id AS proc, r.step AS step, n.name AS sym, n.filePath AS file ORDER BY p.id, r.step"
all_rows = []
offset = 0
CHUNK = 350
while True:
    rows = q(f"{BASE} SKIP {offset} LIMIT {CHUNK}")
    if not rows:
        break
    all_rows.extend(rows)
    offset += CHUNK
    if len(rows) < CHUNK or offset > 3000:
        break

print("total step rows:", len(all_rows))

flows = defaultdict(list)
for proc, step, sym, file in all_rows:
    try: s = int(step)
    except: s = 999
    flows[proc].append((s, sym, file))

out = {p: [{"step": s, "sym": sy, "file": f} for s, sy, f in sorted(v)] for p, v in flows.items()}
json.dump(out, open('/tmp/nexus_flow_chains.json','w'), indent=1)
print("flows:", len(out))
for k in ['proc_205_chat_completions','proc_107_unified_route_v5','proc_186_chat_completions']:
    if k in out:
        print("\n---", k)
        for s in out[k]:
            print(f"   {s['step']}. {s['sym']}  ({s['file']})")
