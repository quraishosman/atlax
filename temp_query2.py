import sqlite3, pandas as pd
conn = sqlite3.connect('data/journal.sqlite')
print(pd.read_sql_query("SELECT * FROM pending_approvals WHERE candidate_id = '4b350709-2dad-42a1-875d-04b2dc1ded71'", conn).to_dict('records'))
