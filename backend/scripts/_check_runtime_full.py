import sqlite3, os, json
from collections import Counter
db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "autoteams.db")
conn = sqlite3.connect(db_path)
c = conn.cursor()

# tables
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print("TABLES:", tables)

# users
c.execute("SELECT id, email, enterprise_id FROM users")
print("\nUSERS:")
for r in c.fetchall():
    print("  ", r)

# enterprise_runtimes
c.execute("SELECT enterprise_id, version, is_active FROM enterprise_runtimes")
print("\nRUNTIMES:")
for r in c.fetchall():
    print("  ", r)

c.execute("SELECT enterprise_id, version, runtime_data FROM enterprise_runtimes WHERE is_active=1")
row = c.fetchone()
if row:
    data = json.loads(row[2]) if isinstance(row[2], str) else row[2]
    print("\n=== ACTIVE RUNTIME ===")
    cg = data.get("collaboration_graph") or {}
    print("collaboration_graph keys:", list(cg.keys()))
    print("collaboration_graph nodes:", len(cg.get("nodes", [])) if isinstance(cg, dict) else "n/a")
    print("collaboration_graph edges:", len(cg.get("edges", [])) if isinstance(cg, dict) else "n/a")
    tr = data.get("tool_registry", [])
    print("tool_registry:", len(tr))
    for t in tr[:20]:
        print("   tool:", t.get("name"), t.get("tool_type"), "installed:", t.get("installed"))
    org = data.get("organization") or {}
    depts = org.get("departments", [])
    print("departments:", len(depts))
    for d in depts[:40]:
        print("   dept:", d.get("name"), "| parent:", d.get("parent_dept_id"), "| level:", d.get("level"), "| id:", d.get("dept_id"))
    agents = data.get("agents", [])
    print("agents:", len(agents))
    for a in agents[:5]:
        print("   agent:", a.get("agent_name"), a.get("department"))
    pes = data.get("process_engines", [])
    print("process_engines:", len(pes))
    for p in pes[:10]:
        print("   pe:", p.get("process_id"), "| type:", p.get("process_type"), "| steps:", [s.get("name") for s in p.get("steps", [])])

conn.close()