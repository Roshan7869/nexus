import subprocess, json

Q = ['node', '.gitnexus/run.cjs', 'cypher', '-r', 'nexus']

def raw(query, timeout=60):
    r = subprocess.run(Q + [query], capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(r.stdout.strip())
    except Exception:
        return {"error": r.stdout.strip()[:400]}

# Enumerate node tables via Kuzu catalog
out = {}
for probe in [
    "CALL TABLE_INFO('CodeNode') RETURN *",
    "MATCH (n) RETURN label(n) AS l, count(*) AS c ORDER BY c DESC",
]:
    out[probe] = raw(probe)
    if 'error' not in out[probe]:
        break

# Count each known label explicitly
labels = ["Function","Class","Method","File","Module","Directory","Route","Tool",
          "Process","Community","Property","Variable","Interface","Type","CodeNode"]
counts = {}
for L in labels:
    d = raw(f"MATCH (n:{L}) RETURN count(*) AS c")
    if 'error' not in d:
        counts[L] = d

print(json.dumps({"catalog_probe": list(out.keys()), "label_counts": counts}, indent=2)[:3000])
