# ATLAX Telegram Localization

Status: Active
Scope: Subscriber-facing Telegram delivery

## Supported Languages

ATLAX currently supports:

- `en`: English, the default fallback.
- `sw`: Kiswahili.

The user’s explicit language choice takes priority over Telegram’s optional
`language_code`. Telegram metadata is captured for future language suggestions,
but ATLAX does not silently change a user’s saved preference.

## User Experience

- New users see a `🌐 Language` button on the welcome screen.
- Registered users see the same control on the home screen and in Preferences.
- The choice is saved per Telegram chat ID.
- A choice made before payment or invitation activation is retained when the
  subscriber is later registered.
- Missing, unsupported, or corrupt values fall back to English.

## Translation Boundary

Translations live in `core/alerts/i18n.py`. Subscriber-facing code should use
the centralized `translate()` helper instead of adding language-specific
branches in the scanner.

Trading symbols, timeframes, prices, confidence scores, transaction IDs,
references, and rule identifiers remain unchanged. They are data, not prose.

All outbound subscriber messages now pass through the Telegram delivery
boundary. This includes `send_reply`, inline menu creation, inline menu edits,
subscriber alerts, and FVG alerts. Therefore subscription screens, payment
instructions, payment references, settings, the paginated public FAQ, Signal
Passport daily/weekly reports, and button labels use the saved language without
requiring every scanner call site to implement its own language branch.

The catalog includes the common English copy and button labels used by the
current subscriber experience. Dynamic trading data such as symbols,
timeframes, prices, confidence scores, transaction IDs, references, and rule
identifiers remains unchanged. A phrase that has not yet been cataloged stays
in English as a safe fallback and must be added to `core/alerts/i18n.py` when
new subscriber copy is introduced.

## Persistence

Language preferences are stored in `data/language_preferences.json` for users
who have not registered yet. Registered subscribers also retain the value in
their settings record. The file contains only Telegram chat IDs and supported
language codes; it must not contain credentials or payment secrets.

## Fallback and Testing

Every translation key must have an English value. Unsupported language values
must normalize to `en`. Tests must cover the selector buttons, persistence
across reload, localized welcome rendering, and English fallback behavior.
