import sqlite3, json, os
from collections import Counter
db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "autoteams.db")
conn = sqlite3.connect(db_path)
c = conn.cursor()
c.execute("SELECT runtime_data FROM enterprise_runtimes WHERE is_active=1")
d = json.loads(c.fetchone()[0])
pe = d.get('process_engines', [])
# Show name + process_type + first step names for each
for p in pe[:15]:
    print("name=", repr(p.get('name')), "| type=", p.get('process_type'), "| steps=", [s.get('name') for s in p.get('steps', [])])
conn.close()