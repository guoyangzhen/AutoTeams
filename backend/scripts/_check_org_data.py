import sqlite3, json, os
db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "autoteams.db")
conn = sqlite3.connect(db)
c = conn.cursor()
c.execute("SELECT version, completeness, runtime_data, is_active FROM enterprise_runtimes WHERE enterprise_id='9e512a5b-5ae1-4e52-82ae-b1bb8d5a550e' ORDER BY created_at DESC LIMIT 3")
rows = c.fetchall()
print("num rows:", len(rows))
for version, comp, data, active in rows:
    d = json.loads(data) if data else {}
    org = d.get('organization', {})
    depts = org.get('departments', [])
    print(f"--- version={version} completeness={comp} is_active={active} num_depts={len(depts)} ---")
    if depts:
        print(json.dumps(depts[:3], ensure_ascii=False, indent=2))
conn.close()