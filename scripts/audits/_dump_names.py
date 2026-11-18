import subprocess, json, time

Q = ['node', '.gitnexus/run.cjs', 'cypher', '-r', 'nexus']
names = []
PAGE = 3000
skip = 0

def run(query, timeout=200):
    r = subprocess.run(Q + [query], capture_output=True, text=True, timeout=timeout)
    return r.stdout

t0 = time.time()
while True:
    q = (f"MATCH (n) RETURN n.id AS id, n.name AS name ORDER BY n.id SKIP {skip} LIMIT {PAGE}")
    out = run(q)
    try:
        d = json.loads(out)
    except Exception as e:
        print("parse fail at skip", skip, out[:200]); break
    md = d.get('markdown', '')
    md = md.replace('\\n', '\n')
    lines = [l for l in md.split('\n') if l.startswith('|')]
    rows = [l.strip().strip('|').split('|') for l in lines[2:]]
    rows = [r for r in rows if len(r) >= 2]
    if not rows:
        break
    for r in rows:
        names.append((r[0].strip(), r[1].strip()))
    print(f"skip={skip} got={len(rows)} total={len(names)} t={time.time()-t0:.0f}s")
    if len(rows) < PAGE:
        break
    skip += PAGE

with open('/tmp/nexus_node_names.txt', 'w') as f:
    for i, n in names:
        f.write(f"{i}\t{n}\n")
print("TOTAL NAMES:", len(names))
