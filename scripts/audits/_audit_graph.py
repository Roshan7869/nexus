import subprocess, json, re

Q = ['node', '.gitnexus/run.cjs', 'cypher', '-r', 'nexus']
OUT = {}

def q(query, key):
    r = subprocess.run(Q + [query], capture_output=True, text=True, timeout=60)
    raw = r.stdout.strip()
    try:
        d = json.loads(raw)
    except Exception:
        OUT[key] = {"error": raw[:300]}
        return
    OUT[key] = d

# 1. node labels + counts (use _label property)
q("MATCH (n) RETURN n._label AS label, COUNT(n) AS c ORDER BY c DESC", "node_labels")
# 2. edge types + counts (CodeRelation.type)
q("MATCH ()-[r:CodeRelation]->() RETURN r.type AS etype, COUNT(r) AS c ORDER BY c DESC", "edge_types")
# 3. process types + counts
q("MATCH (p:Process) RETURN p.processType AS ptype, COUNT(p) AS c ORDER BY c DESC", "process_types")
# 4. stepCount distribution for processes
q("MATCH (p:Process) RETURN p.stepCount AS steps, COUNT(p) AS c ORDER BY steps", "process_steps")
# 5. all routes
q("MATCH (rt:Route) RETURN rt.id AS id, rt.method AS m, rt.filePath AS f ORDER BY rt.filePath", "routes")
# 6. all tools
q("MATCH (t:Tool) RETURN t.name AS name, t.filePath AS f ORDER BY t.filePath", "tools")
# 7. communities (top by symbolCount)
q("MATCH (c:Community) RETURN c.id AS id, c.label AS label, c.cohesion AS coh, c.symbolCount AS syms ORDER BY c.symbolCount DESC LIMIT 60", "communities_top")

with open('/tmp/nexus_graph_audit.json','w') as fh:
    json.dump(OUT, fh, indent=2)

for k,v in OUT.items():
    if isinstance(v, dict) and 'error' in v:
        print(f"[ERR] {k}: {v['error'][:160]}")
    elif isinstance(v, dict):
        print(f"[OK ] {k}: rows={v.get('row_count','?')}")
    else:
        print(f"[?? ] {k}: {type(v)}")
