import json, os

G = json.load(open('/tmp/nexus_graph_audit.json'))
F = json.load(open('/tmp/nexus_flows.json'))
C = json.load(open('/tmp/nexus_flow_chains.json'))

def md(d):
    return d.get('markdown','').replace('\\n','\n') if isinstance(d, dict) else str(d)

lines = []
A = lines.append

A("# NEXUS — GitNexus Graph Audit Evidence Pack")
A("Generated from live GitNexus index (repo `nexus`, ladybugdb backend, schemaVersion 5)")
A("")
A("Index metadata: files=476, nodes=8944, edges=15688, communities=380, processes=300")
A("Indexed commit: 2dfb62d  |  HEAD at audit: c8782ad (STALE by 1 commit)")
A("")
A("-------------------- PART 1 — NODE INVENTORY --------------------")
A("")
A("| node label | count |")
A("| --- | --- |")
for L,c in [("Variable",1712),("Method",1354),("Function",1201),("Property",986),
            ("File",476),("Class",397),("Community",323),("Process",300),
            ("Tool",98),("Route",66),("Module",0),("Interface",0)]:
    A(f"| {L} | {c} |")
A("")
A("NOTE: Module=0 and Interface=0 — these abstractions are ABSENT from the graph.")
A("")
A("-------------------- PART 2 — EDGE INVENTORY --------------------")
A("")
A(md(G['edge_types']))
A("")
A("-------------------- PART 3 — ROUTES (HTTP surface) --------------------")
A("")
A(md(G['routes']))
A("")
A("-------------------- PART 4 — TOOLS (MCP surface) --------------------")
A("")
A(md(G['tools']))
A("")
A("-------------------- PART 5 — COMMUNITIES (functional areas) --------------------")
A("")
A(md(G['communities_top']))
A("")
A("-------------------- PART 6 — PROCESSES / FLOWS --------------------")
A("")
A(f"Total processes: 300. Types:")
A(md(G['process_types']))
A("")
A("Step-count distribution:")
A(md(G['process_steps']))
A("")
A("### Representative flow chains (entry -> terminal, step order)")
A("")
for k in ['proc_205_chat_completions','proc_107_unified_route_v5','proc_186_chat_completions',
          'proc_0_harness_study_projec','proc_163_ide_event']:
    if k in C:
        A(f"**{k}**")
        for s in C[k]:
            A(f"   {s['step']}. {s['sym']}  ({s['file']})")
        A("")
A("")
A("-------------------- PART 7 — GAP PROBES (zero-definition primitives) --------------------")
A("")
A("Direct filePath substring probes against the index — ALL returned ZERO nodes:")
for t in ["fiber","dispose","coeffect","isolate","intercept","hot-swap","mount","unmount","reactiv"]:
    A(f"   - '{t}' -> 0 nodes, 0 flows")
A("")
A("Present but SEMANTICALLY UNRELATED (noise-aligned, must not be mistaken for the concept):")
A("   - 'lifecycle' -> server/catalog_lifecycle.py, scripts/run_catalog_lifecycle.py (catalog, not plugins)")
A("   - 'registry'  -> providers/registry.py (provider registry, not a DI/service container)")
A("")
A("CONCLUSION OF GRAPH AUDIT:")
A("NEXUS has a rich static call graph (2953 CALLS, 3680 DEFINES) and 300 traced execution")
A("flows, but ZERO nodes or edges exist for: dependency injection / service container,")
A("plugin lifecycle, effect tracking / reversible disposal, middleware interception,")
A("and hot-swap. The dynamic-composition layer is structurally absent.")
A("")

open('/home/roshan/nexus/_GRAPH_AUDIT.md','w').write('\n'.join(lines))
print("wrote _GRAPH_AUDIT.md:", len('\n'.join(lines)), "chars")
print("flows in chains file:", len(C))
