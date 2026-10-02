import sqlite3, json, os
db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "autoteams.db")
conn = sqlite3.connect(db_path)
c = conn.cursor()
c.execute("PRAGMA table_info(knowledge_graphs)")
print("columns:", [r[1] for r in c.fetchall()])
c.execute("SELECT graph_data FROM knowledge_graphs LIMIT 1")
row = c.fetchone()
if row:
    d = json.loads(row[0])
    print("graph keys:", list(d.keys()))
    nodes = d.get('nodes', [])
    print("total nodes:", len(nodes))
    # count by type
    from collections import Counter
    types = Counter(n.get('node_type') for n in nodes)
    print("node types:", dict(types))
    # Sample ROLE nodes
    roles = [n for n in nodes if n.get('node_type') == 'ROLE']
    print("\n=== ROLE sample (first 15) ===")
    for n in roles[:15]:
        print(f"  {n.get('node_id')} | {n.get('name')} | attrs={json.dumps(n.get('attributes', {}), ensure_ascii=False)[:200]}")
    # Sample PROCESS nodes
    procs = [n for n in nodes if n.get('node_type') == 'PROCESS']
    print(f"\n=== PROCESS nodes ({len(procs)}) ===")
    for n in procs[:20]:
        print(f"  {n.get('node_id')} | name={n.get('name')!r} | attrs={json.dumps(n.get('attributes', {}), ensure_ascii=False)[:150]}")
    # Sample DEPARTMENT nodes
    depts = [n for n in nodes if n.get('node_type') == 'DEPARTMENT']
    print(f"\n=== DEPARTMENT nodes ({len(depts)}) ===")
    for n in depts[:15]:
        print(f"  {n.get('node_id')} | {n.get('name')} | attrs={json.dumps(n.get('attributes', {}), ensure_ascii=False)[:150]}")
conn.close()