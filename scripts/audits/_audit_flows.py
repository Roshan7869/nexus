import subprocess, json

Q = ['node', '.gitnexus/run.cjs', 'cypher', '-r', 'nexus']
OUT = {}

def q(query, key):
    r = subprocess.run(Q + [query], capture_output=True, text=True, timeout=90)
    try:
        OUT[key] = json.loads(r.stdout.strip())
    except Exception:
        OUT[key] = {"error": r.stdout.strip()[:300]}

# All 300 processes with entry/terminal symbols
q("MATCH (p:Process) RETURN p.id AS id, p.label AS label, p.processType AS ptype, p.stepCount AS steps, p.entryPointId AS entry, p.terminalId AS terminal ORDER BY p.id", "processes")
# All communities with symbol counts
q("MATCH (c:Community) RETURN c.id AS id, c.label AS label, c.cohesion AS coh, c.symbolCount AS syms ORDER BY c.symbolCount DESC", "communities")
# ENTRY_POINT_OF edges: process -> symbol
q("MATCH (p:Process)-[r:CodeRelation]->(n) WHERE r.type='STEP_IN_PROCESS' RETURN p.id AS proc, r.step AS step, n.name AS sym, n.filePath AS file ORDER BY p.id, r.step", "proc_steps")
# MEMBER_OF: symbols -> communities
q("MATCH (n)-[r:CodeRelation]->(c) WHERE r.type='MEMBER_OF' RETURN c.id AS comm, COUNT(n) AS members ORDER BY members DESC", "comm_members")

with open('/tmp/nexus_flows.json','w') as fh:
    json.dump(OUT, fh, indent=2)

for k,v in OUT.items():
    if isinstance(v, dict) and 'error' in v:
        print(f"[ERR] {k}: {v['error'][:200]}")
    else:
        print(f"[OK ] {k}: rows={v.get('row_count','?')}")
