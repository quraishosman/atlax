import sqlite3, pandas as pd, json
conn = sqlite3.connect('data/journal.sqlite')
df = pd.read_sql_query("SELECT request_json FROM pending_approvals WHERE candidate_id = '1e1338f0-6d21-4ea1-8e6b-3b11b111bd28'", conn)
if not df.empty:
    req = json.loads(df.iloc[0]['request_json'])
    print(json.dumps(req['risk_evaluation'], indent=2))
else:
    print("Not found")
