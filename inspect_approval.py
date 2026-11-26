import sqlite3, pandas as pd

conn = sqlite3.connect('data/approval_queue.sqlite')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("Tables:", cur.fetchall())

df = pd.read_sql_query("SELECT * FROM approval_queue ORDER BY rowid DESC LIMIT 5", conn)
print(df.to_string())
