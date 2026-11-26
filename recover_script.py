import json
import sys

transcript_path = r'C:\Users\HP\.gemini\antigravity\brain\7e636f0e-0e44-48c1-b7a7-388a6e41325d\.system_generated\logs\transcript_full.jsonl'

found_write = False
with open(transcript_path, 'r', encoding='utf-8') as f:
    for line in f:
        if 'telegram_channel.py' in line and '"write_to_file"' in line:
            try:
                data = json.loads(line)
                if data.get('type') == 'PLANNER_RESPONSE':
                    for call in data.get('tool_calls', []):
                        if call.get('name') == 'write_to_file':
                            args = call.get('args', {})
                            if 'telegram_channel.py' in args.get('TargetFile', ''):
                                print(f"Found write_to_file for {args['TargetFile']}")
                                with open('recovered_telegram_channel.py', 'w', encoding='utf-8') as out:
                                    out.write(args['CodeContent'])
                                found_write = True
            except Exception as e:
                pass

if found_write:
    print('Recovery script finished.')
else:
    print('No write_to_file found. Searching for edit commands...')
    # Let's see if there are replace commands that we can replay, or if we can find the file content directly in a tool response.
    # When view_file returns, the tool result has the file content.
    with open(transcript_path, 'r', encoding='utf-8') as f:
        for line in reversed(list(f)):
            if 'Total Lines: 1149' in line and 'File Path' in line and 'telegram_channel.py' in line:
                try:
                    data = json.loads(line)
                    if data.get('type') == 'TOOL_RESULT':
                        content = data.get('content', '')
                        if 'Total Lines: 1149' in content:
                            print("Found a view_file result for telegram_channel.py with 1149 lines!")
                            # We can extract what we can, but view_file usually only has a portion unless StartLine/EndLine weren't specified.
                            with open('recovered_view_file.txt', 'w', encoding='utf-8') as out:
                                out.write(content)
                except Exception:
                    pass
