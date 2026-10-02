import sqlite3

c = sqlite3.connect('backend/autoteams.db')
print("columns:", [r[1] for r in c.execute("PRAGMA table_info(audit_logs)")])
for r in c.execute("SELECT action, resource_type, resource_id FROM audit_logs WHERE resource_type='local_path' ORDER BY created_at DESC LIMIT 8"):
    print(r)