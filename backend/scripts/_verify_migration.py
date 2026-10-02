import sqlite3, os, sys

db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "autoteams.db")
print("DB:", db_path)
conn = sqlite3.connect(db_path)
c = conn.cursor()
c.execute("SELECT version_num FROM alembic_version")
print("alembic_version:", c.fetchall())
for t in ["llm_api_configs", "users", "compilation_jobs", "enterprise_runtimes"]:
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,))
    print(f"{t} exists:", bool(c.fetchall()))
# columns of llm_api_configs
c.execute("PRAGMA table_info(llm_api_configs)")
cols = c.fetchall()
print("llm_api_configs columns:", [x[1] for x in cols])
conn.close()