import sqlite3, os, json
db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "autoteams.db")
conn = sqlite3.connect(db_path)
c = conn.cursor()

# active runtime completeness
c.execute("SELECT version, runtime_data FROM enterprise_runtimes WHERE is_active=1")
row = c.fetchone()
if row:
    data = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    print("active runtime version:", row[0])
    print("completeness:", data.get("completeness"))
    print("key budget:", list(data.keys()))
    org = data.get("organization") or {}
    print("departments:", len(org.get("departments", [])))
    print("agents:", len(data.get("agents", [])))
    print("process_engines:", len(data.get("process_engines", [])))
    print("tool_registry:", len(data.get("tool_registry", [])))
    print("collaboration_graph:", type(data.get("collaboration_graph")), len(data.get("collaboration_graph", {})) if isinstance(data.get("collaboration_graph"), dict) else '')
    ki = data.get("knowledge_index") or {}
    print("knowledge_index:", ki)

# interview sessions
print("\ninterview_sessions sample:")
c.execute("SELECT id, enterprise_id, status, answered_count, total_count FROM interview_sessions LIMIT 5")
for r in c.fetchall():
    print("  ", r)

conn.close()