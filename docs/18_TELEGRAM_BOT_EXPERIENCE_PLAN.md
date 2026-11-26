# ATLAX Telegram Bot Experience Plan

Status: Draft implementation plan
Date: 2026-08-08
Scope: Telegram onboarding, subscriber experience, payments, signal access, and future Mini App

Implementation status:

- Phase 0: Accepted in `docs/decisions/ADR-005.md`.
- Phase 1: Source implementation and focused tests complete; live scanner reload pending.
- Phase 2: Profile identity, preference acknowledgements, read-only signal filters, and stored signal detail views implemented; live scanner reload pending.
- Phase 3: Subscription plan card, expiry-aware renewal messaging, payment timelines/history, idempotent transitions, and admin queue/history implemented; live scanner reload pending.
- Phase 4: Restricted admin operations console, subscriber deactivation confirmation, system health view, and redacted append-only operational audit log implemented; live scanner reload pending.
- Phase 5: Mini App bootstrap/session, subscriber-scoped signal terminal, analytics snapshot, preferences, payment history, inline launch button, and persistent Telegram menu registration implemented; public HTTPS URL configuration remains deployment-dependent.
- Phase 6: Focused API/Mini App, payment lifecycle, Telegram callback, and audit tests pass in Docker; Docker API/dashboard stack rebuilt and healthy. Full native MT5 scanner validation remains environment-dependent.

## 1. Product Direction

ATLAX should feel like a compact trading terminal inside Telegram, not a collection of commands and not a copy of a Solana execution bot.

BONKbot's public product surfaces show the useful pattern to borrow: persistent user state, fast action buttons, wallet or account status, real-time alerts, portfolio context, and automation controls. Its public features include funding, token selection, one-click actions, real-time alpha alerts, limit orders, trailing stops, and automated strategies. ATLAX should adapt the interaction pattern to market intelligence and subscriber signal delivery, while preserving ATLAX's MT5-only execution boundary.

ATLAX must not add self-custody wallets, private-key handling, Solana execution, or undocumented trading behavior merely to imitate BONKbot. Those are separate product decisions and are outside this plan.

## 2. Research Basis

- [BONKbot official product site](https://bonkbot.io/) - fast actions, wallet state, token entry, alpha alerts, limit orders, trailing stops, and automation.
- [BONKbot documentation](https://docs.bonkbot.io/) - wallet, trading, security, fee, and tutorial-oriented product structure.
- [Telegram Bot Features](https://core.telegram.org/bots/features) - commands, reply keyboards, inline keyboards, menu buttons, deep links, and standard `/start`, `/help`, and `/settings` behavior.
- [Telegram Mini Apps](https://core.telegram.org/bots/webapps) - full interfaces launched from inline buttons or the bot menu, theme integration, payments, and server-side `initData` validation.
- [Telegram Bot Payments](https://core.telegram.org/bots/payments) - invoices and third-party payment providers.

## 3. User States

The bot must render a different home experience from server-side state. It must never guess access from the message text or from a client-supplied role.

| State | Home experience | Primary action |
|---|---|---|
| New user | Welcome, product explanation, plan, and support | Subscribe |
| Invite user | Welcome, profile setup, access status, and signal preview | Open Signal Terminal |
| Active paid user | Personalized terminal with plan, expiry, profile, and latest signals | View Signals |
| Payment pending | Payment reference, submitted proof, status, and support | Check Payment |
| Expired user | Expiry notice, saved profile, renewal options | Renew Access |
| Admin | Operational console, payment queue, system health, and subscriber tools | Review Queue |

Required persisted identity fields:

- Telegram chat ID.
- Username and display name when available.
- Telegram language code when available.
- First-seen and last-seen timestamps.
- Onboarding state.
- Last rendered menu message ID when useful for edit-in-place navigation.

## 4. First-Run Experience

`/start` should produce a real welcome screen rather than only the sentence `Your subscriber menu`.

### New or unregistered user

The first message should contain:

- A short ATLAX value statement: deterministic market intelligence and Telegram signal delivery.
- What the subscription includes: signal access, selected trading profile, configurable symbols, confidence filter, and alert preferences.
- Price and duration from the Settings Engine.
- Support contact from configuration.
- A clear note that ATLAX does not promise profit and that signals are advisory unless an approved document says otherwise.

Primary buttons:

- `View Plans`
- `Pay with Crypto`
- `Pay with M-Pesa`
- `How ATLAX Works`
- `Public FAQ`
- `Contact Support`

### Active user

The first message should greet the user by display name when available and show a compact account card:

- Access status.
- Plan source.
- Expiry date or `No expiry set` for legacy invite access.
- Trading style.
- Timeframes.
- Selected symbol filter.
- Minimum confidence setting.
- Notification mode.
- Latest signal count or latest alert timestamp when available.

Primary buttons:

- `Open Signal Terminal`
- `My Profile`
- `Subscription`
- `Preferences`
- `Market Context`
- `Help & Support`

The response should be rendered with an inline keyboard and should edit the same menu message during navigation where possible. Telegram recommends inline keyboards for behind-the-scenes navigation and editing the existing keyboard for a smoother experience.

## 5. Main Telegram Information Architecture

### Signal Terminal

Read-only subscriber experience for:

- Latest eligible signals.
- Unread or recent alerts.
- Signal detail by alert ID.
- Pair filter.
- Timeframe filter.
- Profile filter.
- Confidence and status display.
- Explanation of detector, strategy, confidence, and risk fields that already exist in ATLAX records.

The bot must display stored signal data. It must not calculate new CRT rules, invent a confidence score, or turn detector output directly into a trade instruction.

### My Profile

Display and edit:

- Display name and Telegram identity.
- Active plan and expiry.
- Trading style.
- Routed timeframes.
- Symbol filter.
- Minimum confidence preference.
- Notification mode.
- Optional major key-level-only signal filter.
- Last updated timestamp.

Every change should be validated, persisted through the existing subscriber/settings boundary, logged, and acknowledged with a concise confirmation.

### Subscription

Use the current `PaymentService` as the business boundary and improve its presentation:

- Plan card with price and duration.
- Crypto and M-Pesa method cards.
- New payment reference.
- Copy-friendly wallet or number details.
- Payment status timeline: created, awaiting payment, submitted, under review, confirmed, rejected, or expired.
- Renewal that extends an existing active subscription instead of overwriting it.
- Expiry reminders from configurable policy.
- Admin review queue with approve/reject audit records.

The current external-rail/manual-review decision in `docs/decisions/ADR-004.md` remains the authority. Automatic crypto or M-Pesa confirmation requires a separately approved provider adapter and verification design.

Telegram also supports payments and paid subscriptions, but the payment rail must be chosen deliberately. Telegram's official guidance says digital transactions can use Telegram Stars, while the Bot Payments API supports connected third-party providers. ATLAX should not silently mix manual wallet instructions, Telegram invoices, and automatic activation in one unreviewed flow.

### Market Context

Expose existing read-only market context:

- Current heatmap.
- Currency strength summary.
- Economic calendar summary.
- Active market session when already available from configuration or stored context.

No new market interpretation should be introduced here without a documented authority.

### Analytics and AI Advisory

Expose existing advisory capabilities only:

- Daily performance.
- Seven-day and thirty-day performance.
- Best and worst pair.
- Win rate and PnL summaries.
- Journal-based questions.
- Daily report.

AI remains advisory. It cannot detect CRT, choose an entry, override risk, change configuration, approve a payment, or execute a trade.

### Help and Support

Provide:

- How to subscribe.
- How to submit payment proof.
- What each trading style means.
- How signals are routed.
- How to change preferences.
- Risk disclaimer.
- Support contact.
- System status message when a dependency is unavailable.

## 6. Menu and Callback Contract

The current bot has menu behavior embedded in `MarketScanner`. The implementation should extract Telegram presentation and interaction handling into a focused adapter under the Telegram or alert-delivery boundary. The scanner should remain the orchestrator and should not become the permanent owner of every conversation screen.

Proposed callback namespaces:

- `nav:home`
- `signals:list`
- `signals:detail:<alert_id>`
- `signals:filter:<kind>:<value>`
- `profile:home`
- `profile:style:<style>`
- `profile:symbols:<preset>`
- `profile:confidence:<value>`
- `profile:notifications:<mode>`
- `billing:home`
- `billing:method:<method>`
- `billing:status`
- `billing:submit:<reference>`
- `admin:payments`
- `admin:payment:approve:<reference>`
- `admin:payment:reject:<reference>`

Every callback must:

- Be parsed as a typed, allow-listed action.
- Re-check the user identity and role server-side.
- Be idempotent where it changes state.
- Answer the callback query.
- Log the action and result without secrets.
- Fail closed for unknown or unauthorized actions.

## 7. Backend Components

Proposed implementation boundaries:

- `core/telegram/telegram_application.py` - update polling and command/callback dispatch.
- `core/telegram/menu_renderer.py` - state-aware message and keyboard rendering.
- `core/telegram/callback_contracts.py` - typed callback parsing and allow-listing.
- `core/telegram/profile_service.py` - profile and preference reads/writes.
- `core/telegram/signal_feed_service.py` - read-only access to stored alert history.
- `core/telegram/onboarding_service.py` - first-run and lifecycle messaging.
- `core/telegram/audit_service.py` - user interaction and admin action audit events.
- `core/payments/payment_service.py` - retain payment state transitions and provider-independent business rules.

The first extraction should be behavior-preserving. Existing CRT detection, strategy, confidence, risk, execution, analytics, and alert routing logic must remain unchanged.

## 8. Mini App Direction

Chat menus are best for quick actions. A richer signal terminal, analytics views, filters, charts, payment history, and profile editing should move into the existing dashboard as a Telegram Mini App in a later phase.

Recommended approach:

1. Reuse the existing dashboard frontend rather than creating a second UI codebase.
2. Add an `Open ATLAX` inline button and configure the Telegram menu button after the Mini App is deployed over HTTPS.
3. Authenticate using Telegram `WebApp.initData`.
4. Validate `initData` server-side with the bot token and reject stale or invalid data.
5. Do not persist long-lived bearer tokens in browser `localStorage`.
6. Use short-lived server sessions or secure cookies, with explicit expiry and revocation.
7. Apply Telegram theme variables and mobile-first safe-area behavior.

Telegram explicitly warns that `initDataUnsafe` must not be trusted and that the raw `initData` must be validated by the backend before user identity is accepted.

## 9. Security and Compliance Boundaries

- Telegram chat ID is an identity input, not an authorization claim.
- Admin role is derived only from server-side configuration or approved role storage.
- Payment approval is admin-only and auditable.
- Payment references and transaction IDs are treated as untrusted input.
- Wallet addresses and M-Pesa numbers are configuration values, never hardcoded in UI logic.
- Telegram bot tokens, webhook secrets, JWT secrets, and provider credentials remain environment-only.
- No private keys are stored or accepted by ATLAX.
- No AI model receives direct database, filesystem, broker, payment, or configuration credentials.
- Every important user, payment, configuration, and admin action is logged.
- Error responses do not expose secrets or internal stack traces.

## 10. Phased Implementation Plan

### Phase 0 - Product and contract freeze

- Approve this document.
- Add an ADR for chat-first navigation plus future Mini App.
- Define the role/state matrix.
- Define the typed callback contract.
- Define signal-card fields from existing data models.
- Define payment rail behavior and verification states.

Deliverables: approved specification, ADR, callback contract, acceptance test matrix.

### Phase 1 - Welcome and navigation

- Create personalized `/start` messages for every user state.
- Add a persistent role-aware home menu.
- Register complete command descriptions with Telegram.
- Add `/help`, `/faq`, `/settings`, `/status`, `/subscribe`, `/renew`, `/signals`, `/profile`, `/support`, and `/notifications` behavior.
- Add `/history` and `/weekly` as read-only Signal Passport views.
- Replace duplicate chat messages with edit-in-place navigation where appropriate.
- Treat account-setup questions and completed account-selection cards as
  one-time UI: delete the bot-owned prompt/card after it is consumed, while
  preserving normal navigation screens until the user leaves them. Never
  delete or retain the user's message content merely to clean up the UI.

Deliverables: usable first-run bot and stable navigation tests.

### Phase 2 - Profile and signal terminal

- Add profile cards and preference controls.
- Add read-only signal feed and signal detail views.
- Add pair, timeframe, and confidence filters using configured values.
- Add unread/recent alert state if the data model supports it.
- Add delivery preference confirmation and audit events.

Deliverables: active users can understand their account and inspect the signals they receive.

### Phase 3 - Subscription productization

- Replace the basic payment text with a plan and payment-status experience.
- Add expiry and renewal messaging.
- Add idempotent payment status transitions.
- Add admin payment queue and review history.
- Add provider adapters only after a separate verification specification is approved.

Deliverables: deterministic subscription lifecycle with clear receipts and no silent activation.

### Phase 4 - Admin operations

- Add a restricted admin console for pending payments, subscribers, alert health, and system status.
- Add safe subscriber lookup and deactivation controls.
- Add audit-log views for approvals, profile changes, and operational actions.
- Keep emergency stop and risk controls separate from subscriber-facing menus.

Deliverables: support and operations can run the service without editing JSON files manually.

### Phase 5 - Mini App terminal

- Reuse the dashboard as a Telegram Mini App.
- Implement validated Telegram identity bootstrap.
- Add responsive signal cards, charts, analytics, subscription history, and settings.
- Configure the Telegram menu button and `Open ATLAX` launch button.
- Verify mobile and desktop Telegram layouts.

Implementation notes:

- `POST /api/miniapp/bootstrap` accepts only Telegram `WebApp.initData`, validates its signature and freshness, and issues a short-lived HttpOnly `atlax_miniapp_session` cookie.
- Mini App API routes never accept the dashboard bearer session and do not expose payment destinations, transaction proofs, lot sizes, or execution controls.
- The terminal is available at `/miniapp` in the existing dashboard and uses the dashboard's Vite build.
- The bot now adds an `Open ATLAX` `web_app` button to home/onboarding menus and registers the persistent chat menu button at scanner startup when `ATLAX_TELEGRAM_MINI_APP_URL` is set to the deployed HTTPS `/miniapp` URL.

Deliverables: advanced terminal experience for dense data without making the chat unreadable.

### Phase 6 - Reliability and launch hardening

- Add fake-Telegram integration tests for messages, callbacks, API failures, and retries.
- Add payment idempotency and expiry tests.
- Add role-isolation tests for every admin callback.
- Add Mini App `initData` validation tests.
- Add structured metrics for onboarding conversion, payment completion, menu failures, alert delivery, and churn.
- Run a security review before enabling any automatic payment provider or execution-related action.

Current implementation notes:

- Docker passes the local `.env` into the API container without copying it into the image build context.
- The API image includes `passlib` with `bcrypt<5`, matching the existing password-hash implementation.
- The scanner treats the optional MT5 import as unavailable for non-market-data test paths and fails closed when live MT5-only operations are requested.
- The Vite development proxy defaults to Docker's API port `8080`; override it with `VITE_API_PROXY_TARGET` when using a different local API port.

## 11. Acceptance Criteria

The feature is ready for implementation completion only when:

- A new user receives a useful personalized welcome screen with clear next actions.
- An active subscriber sees profile, subscription, signal, market, and support actions without typing commands.
- Every menu action either edits the current message or clearly explains why a new message was required.
- Profile changes persist and affect only documented alert routing preferences.
- Payment states are visible and deterministic from creation through expiry or approval.
- Admin-only actions reject non-admin users and create audit records.
- Signal cards show stored, explainable data and never invent trading decisions.
- AI output remains advisory.
- Telegram failures are logged with safe diagnostics.
- Tests cover every user state and callback namespace.
- Documentation and configuration are updated before runtime behavior is enabled.

## 12. Explicitly Out Of Scope

- Solana wallet custody or private-key import.
- BONKbot-style token buying, selling, DCA, limit orders, trailing stops, or MEV routing.
- New CRT rules or changes to detector behavior.
- AI-generated live entries, exits, confidence scores, or risk decisions.
- Automatic payment confirmation without an approved provider and verification design.
- Direct Telegram control of MT5 execution.
## Subscriber Routing Controls

After payment approval, the bot presents a required routing choice:

- Scalper: M5
- Day Trader: M15 and H1
- Swing Trader: H4 and D1
- All Styles: M5, M15, H1, H4, and D1

## ATLAX AI

Active subscribers can open `🤖 Ask ATLAX AI` or use `/ask <question>`.
Quick actions cover economic news, the ATLAX heatmap, configured-pair
correlation, aggregate ATLAX signal results, and explanations of how ATLAX
works. The assistant is advisory and cannot access subscriber accounts,
manage trades, or make guaranteed predictions. Its external AI context is
limited to sanitized aggregate signal results, heatmap values, calendar
events, and correlation output.

ATLAX AI abuse controls persist usage in the subscriber record. The
default policy is 10 questions per UTC day, 3 per minute, a 10-second cooldown,
one active request, and automatic AI-only blocking after three security or
illegal-activity violations. Admins can reset a quota or block/unblock AI
without removing signal access.

The Signal Terminal exposes one compact `Pairs` button. It opens a paginated, configuration-derived pair picker with multi-select and `All Pairs`. The selected pairs are persisted in subscriber settings; an empty selection means all configured pairs.
