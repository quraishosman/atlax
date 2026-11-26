from pathlib import Path
import re
patterns = [
    'google_sheets_trade_logged',
    'google_sheets_request_failed',
    'google_sheets_response_error',
    'google_sheets_dependency_missing',
    'WIN ✅',
    'LOSS ❌',
    'FULL TARGET HIT',
    'STOP LOSS HIT',
    'TARGET HIT',
    'TP2'
]
regex = re.compile('|'.join(re.escape(p) for p in patterns))
for path in ['data/atlax_runtime.log', 'data/atlax_runtime.error.log']:
    p = Path(path)
    print('---', path, 'exists' if p.exists() else 'missing', '---')
    if p.exists():
        lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
        matches = [line for line in lines if regex.search(line)]
        print('matches', len(matches))
        for line in matches[-50:]:
            print(line)
print('--- active_predictions.json ---')
act = Path('data/active_predictions.json')
if act.exists():
    text = act.read_text(encoding='utf-8', errors='replace').strip()
    print('size', len(text))
    print(text[:1000])
print('--- daily_stats.json ---')
stats = Path('data/daily_stats.json')
if stats.exists():
    print(stats.read_text(encoding='utf-8', errors='replace'))
