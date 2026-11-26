import sys

file_path = 'core/alerts/telegram_channel.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

start_sig = 'def _build_message(alert: AlertEvent) -> str:'
end_sig = 'def _score_bar(score: float) -> str:'

start_idx = content.find(start_sig)
end_idx = content.find(end_sig)

if start_idx == -1 or end_idx == -1:
    print('Failed to find boundaries')
    sys.exit(1)

new_func = '''def _build_message(alert: AlertEvent) -> str:
    """
    Build a formatted Telegram HTML message for an AlertEvent.

    Keeps the message concise and scannable for a trader on mobile.
    """
    dir_label = "🟢" if alert.direction == "BUY" else "🔴"
    dir_text = "BUY SETUP" if alert.direction == "BUY" else "SELL SETUP"
    score_bar = _score_bar(alert.confidence_score)

    entry = (alert.entry_zone_low + alert.entry_zone_high) / 2
    stop  = alert.invalidation_level
    tp1   = alert.midpoint_target
    tp2   = alert.opposite_extreme_target

    risk   = abs(entry - stop)
    reward = abs(tp2 - entry)
    rr     = float(reward / risk) if risk > 0 else 0.0

    formed_at = alert.created_at.strftime('%Y-%m-%d %H:%M UTC')

    return (
        f"🎯 SETUP DETECTED\\n"
        f"━━━━━━━━━━━━━━━━━━━━\\n"
        f"💎 {alert.symbol} · {alert.timeframe}\\n"
        f"{dir_label} {dir_text}\\n"
        f"━━━━━━━━━━━━━━━━━━━━\\n"
        f"📐 TRADE TARGETS\\n"
        f"🛑 Stop      <code>{_fmt(stop)}</code>\\n"
        f"🪩 TP1       <code>{_fmt(tp1)}</code>\\n"
        f"🏆 TP2       <code>{_fmt(tp2)}</code>\\n"
        f"⚖️ R:R       1 : {_fmt(rr, decimals=1)}\\n"
        f"━━━━━━━━━━━━━━━━━━━━\\n"
        f"🔥 CONFLUENCE\\n"
        f"Strength    {score_bar} {int(alert.confidence_score)}%\\n"
        f"━━━━━━━━━━━━━━━━━━━━\\n"
        f"\\n"
        f"⚡️ ALERT 2 fires when price enters setup zone\\n"
        f"📍 Detected  {formed_at}\\n"
        f"🔖 Setup #{alert.candidate_id[:4].upper()}"
    )

'''

new_content = content[:start_idx] + new_func + '\n\n' + content[end_idx:]

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(new_content)
print('Updated _build_message successfully')
