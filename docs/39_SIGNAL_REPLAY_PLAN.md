# ATLAX Signal Replay and Proof Center Plan

Version: 1.0  
Status: In Progress  
Owner: ATLAX Engineering  
Related documents: `docs/21_SIGNAL_PASSPORT.md`, `docs/06_DATA_MODELS.md`, `docs/12_ANALYTICS_ENGINE.md`, `docs/14_TESTING.md`, `docs/15_LOGGING.md`, `docs/23_POSTGRES_STORAGE.md`

---

## 1. Purpose

Build a deterministic Signal Replay feature that lets a subscriber inspect the
market evidence available when ATLAX recorded a signal and compare it with the
subsequent price outcome.

The feature is based on broker candle data received through the existing
MetaTrader 5 market-data boundary. TradingView is not required and must not be
added as a dependency.

The feature will make the Signal Passport more transparent without changing
how ATLAX detects, scores, routes, approves, risks, executes, or invalidates a
signal.

The central product surface is:

```text
Signal alert -> View Passport -> Replay evidence -> Review outcome
```

## 2. Non-Negotiable Boundaries

- Replay is an Analytics feature.
- Replay is not a detector, strategy, confidence, risk, or execution engine.
- Replay must use only candles that were closed at the time of detection for
  the initial evidence view.
- Future candles may appear only in the post-detection outcome section.
- The replay renderer must never feed data back into live signal decisions.
- Replay must not claim that a signal was profitable when its outcome is
  `UNKNOWN`.
- Subscriber views must not expose broker credentials, account identifiers,
  lot sizes, private PnL, source code, or undocumented internal rules.
- Missing snapshots, incomplete candles, storage failures, and renderer
  failures must return `UNKNOWN` or a clear unavailable state.
- Historical replay is available only for signals captured after this feature
  is enabled, unless a legacy signal has a verified equivalent snapshot.

## 3. Product Definition

### 3.1 Signal Passport

The existing Passport remains the authoritative identity and lifecycle record.
It continues to contain:

- Signal ID.
- Pair, style, timeframe, and UTC creation time.
- Lifecycle status and final outcome.
- Customer-facing invalidation reason when applicable.
- Confidence, ATR quality, key-level, and momentum summaries where approved.

The Passport gains a replay availability state:

- `AVAILABLE`: immutable evidence snapshot is valid and renderable.
- `PENDING`: signal exists but the snapshot or outcome is still being stored.
- `UNAVAILABLE`: snapshot is missing, invalid, or cannot be rendered safely.
- `NOT_APPLICABLE`: legacy signal predates replay capture.

### 3.2 Telegram Experience

Delivered signal messages may include:

```text
[🔎 View Passport] [📼 Replay]
```

The Passport view shows the existing public fields. The Replay action sends a
Telegram chart image with a caption containing:

- Pair and timeframe.
- Formation time in UTC.
- Direction and signal status.
- Entry zone, stop, and targets when already part of the approved signal.
- A clear marker for signal formation.
- Outcome status, or `Outcome is still unknown`.

The first release will use a generated static image sent through Telegram. It
does not require a public HTTPS URL or a Telegram Mini App.

### 3.3 Future Web Replay

A dashboard or Mini App may later provide a candle-by-candle slider. That is a
separate phase and must not block the Telegram image replay. It will require an
approved HTTPS deployment and authenticated Passport access.

## 4. Replay Semantics

### 4.1 Formation Evidence

The formation view contains only data that ATLAX was allowed to use when the
signal was recorded:

- Exactly 30 closed source-timeframe context candles immediately before the
  parent candle for the V1 presentation default. This is a display setting,
  not a detector rule, and is configurable through the Settings Engine.
- The complete closed detector sequence: parent, sweep, and confirmation
  candles. The roles and indexes come from the stored detector output; the
  replay renderer must not infer or relabel them later.
- Closed higher-timeframe candles used by approved momentum or key-level
  evaluation.
- Signal price references already present on the Passport.
- The approved quality and location summaries.

The formation cutoff is the exact UTC candle-close timestamp recorded by the
live pipeline when the signal was created. The snapshot stores this cutoff and
the source broker identity. The renderer must use the exact broker candle data
that the detector saw. It must not re-query a different feed, clean candles,
adjust prices, merge bars, or substitute later historical data.

Formation rendering uses both role labels and visual differences:

- Parent candle: gold outline and `P` label.
- Sweep candle: amber outline and `S` label.
- Confirmation candle: cyan outline and `C` label.
- Bullish candles: green body.
- Bearish candles: red body.
- Neutral context candles: standard gray body.

The role outline and label are required so the chart remains understandable
without relying on color alone. A vertical dark line labeled `FORMATION`
marks the formation cutoff.

When approved signal price references are available, the chart draws:

- Entry zone as a blue translucent band with `ENTRY` label.
- Stop or invalidation level as a red dashed line with `STOP` label.
- TP1 as a green dashed line with `TP1` label.
- TP2 as a darker green dashed line with `TP2` label.

If a price reference is absent, that line is omitted and the caption says
`Price level unavailable`; the renderer must not calculate a replacement.

The CRT three-candle structure is highlighted as one clearly bounded visual
unit:

- The replay must show exactly three role candles: `P` (parent), `S` (sweep),
  and `C` (confirmation).
- A gold rectangle spans from the parent candle's exact low to its exact high.
- A gold horizontal line labeled `P HIGH` begins at the parent candle and
  continues through the confirmation candle.
- A gold horizontal line labeled `P LOW` begins at the parent candle and
  continues through the confirmation candle.
- The right edge of the three-candle block is marked with a vertical line
  labeled `3-CANDLE CRT`.
- The parent, sweep, and confirmation candles retain their `P`, `S`, and `C`
  role labels and distinct outlines.
- The parent high and low values are displayed using the configured symbol
  precision. The renderer reads these values directly from the stored parent
  candle; it must not round them before drawing the range.
- If the detector output does not contain exactly one valid parent, sweep, and
  confirmation candle, the replay is marked `UNKNOWN` and the renderer must
  not infer the roles from candle position.

The parent range marks are visual evidence only. They do not create a new
entry, invalidation, confidence, or filtering rule.

### 4.2 Outcome Evidence

The post-detection section contains candles after the formation timestamp and
the existing deterministic tracker result:

- V1 shows up to 50 closed source-timeframe candles after the formation cutoff,
  or fewer when the stored terminal outcome occurs earlier. The 50-candle
  value is a configurable presentation setting and is not a trading rule.
- `OPEN`: the chart shows the formation evidence and any safely stored forward
  candles, with `Outcome pending`. No result is claimed.
- `RESOLVED` with `WIN`: the existing tracker recorded `tp2_hit`; the chart
  shows a green `WIN` marker on the terminal candle.
- `RESOLVED` with `LOSS`: the existing tracker recorded `sl_hit` or another
  approved invalidation outcome; the chart shows a red `LOSS` or
  `INVALIDATED` marker and the customer-facing reason when available.
- `EXPIRED`: the monitoring window ended without a final target or
  invalidation result; the chart shows `EXPIRED - UNKNOWN OUTCOME`.
- `UNKNOWN`: required outcome data is missing or inconsistent; the chart shows
  `OUTCOME UNKNOWN` and does not color the result as a win or loss.
- `BREAKEVEN` is not created by V1 because the current Passport contract does
  not define a breakeven event. If an approved tracker later emits a
  `BREAKEVEN` terminal event, the renderer will show a neutral breakeven marker
  without changing the historical `WIN` and `LOSS` rules.

The renderer must distinguish formation evidence from later outcome evidence
with a visible time marker. It must not draw future candles in the formation
panel as if they were known at detection time.

Every image includes a small `Data as of YYYY-MM-DD HH:MM UTC` label. For an
open signal this is the latest captured candle timestamp. For a resolved
signal it is the terminal outcome candle timestamp.

### 4.3 Candle-Based Scope

Version 1 replays closed candles. It does not promise tick-by-tick order
sequence, intrabar path, broker slippage, or execution-side account results.
Those details are unavailable unless separately captured and are outside the
subscriber Signal Passport contract.

Forward candles are appended only from the same normalized MT5 broker feed
used by the scanner. If a candle is missing, duplicated, not closed, or has a
source mismatch, the affected portion is marked unavailable and the outcome
remains `UNKNOWN` unless the existing tracker already has an authoritative
terminal result.

## 5. Immutable Snapshot Contract

Each captured snapshot is linked to exactly one `signal_id` and contains:

### Identity

- `snapshot_id`.
- `signal_id`.
- `snapshot_version`.
- `captured_at` in UTC.
- `created_at` from the Signal Passport.

### Market Context

- `symbol`.
- `source_timeframe`.
- `source` set to the normalized MT5 market-data source.
- `broker_timezone` only when needed for audit; public output remains UTC.
- `price_digits` or equivalent symbol precision when available.
- `spread` only when already available and approved for analytics storage.

### Candles

For every stored candle:

- `symbol`.
- `timeframe`.
- `open_time` in UTC.
- `close_time` in UTC.
- `open`, `high`, `low`, `close`.
- `volume` when available.
- `is_closed=true`.
- `source`.

The snapshot must include the source-timeframe detector context and only the
higher-timeframe context that was actually used by an approved evaluator.
The stored candle list includes the 30 pre-parent context candles, the complete
parent/sweep/confirmation sequence, and the post-formation candles captured by
the outcome tracker up to the V1 forward limit. The exact formation cutoff and
each candle's source timestamps are stored; no candle is reconstructed from a
later chart query.

### Decision Context

- Detector and rulebook version identifiers.
- Strategy configuration snapshot identifier.
- Confidence configuration snapshot identifier.
- ATR configuration snapshot identifier when applicable.
- Key-level configuration snapshot identifier when applicable.
- HTF momentum configuration snapshot identifier when applicable.
- Signal direction and price references.
- Sanitized explanation codes and public explanation text.

### Integrity

- Canonical serialization of the snapshot.
- SHA-256 `snapshot_hash`.
- Capture status and bounded failure reason.

The V1 `snapshot_hash` covers the immutable identity, MT5 formation candles,
higher-timeframe evidence, parent range, and rule-context fields. It excludes
the append-only outcome candles and terminal outcome fields so later tracker
updates do not alter the hash of the evidence available at formation time.

Prices and raw candle data are internal analytics data. Public Telegram output
must use only the approved presentation fields.

## 6. Storage Design

Add a repository abstraction for replay snapshots so local and PostgreSQL
backends expose the same behavior.

The Signal Passport storage backend is authoritative: local deployments use
the existing local journal backend, and production uses the approved
PostgreSQL backend after cutover. The file system is not the source of truth
for replay data. It may hold only temporary or derived chart images.

The initial portable schema should contain an immutable
`signal_replay_snapshots` record with:

- `snapshot_id` as the primary key.
- `signal_id` as a unique foreign identity to the Passport.
- `snapshot_version`.
- `symbol`, `source_timeframe`, `created_at`, and `captured_at`.
- `formation_context` as validated structured JSON.
- `outcome_context` as validated structured JSON, initially empty or `UNKNOWN`.
- `rule_context` as sanitized structured JSON.
- `snapshot_hash`.
- `status`.
- `failure_reason`.
- `updated_at`.

The repository must enforce these properties:

- One active immutable snapshot per signal and version.
- Repeated capture requests are idempotent.
- A different snapshot for the same signal cannot silently overwrite the
  original.
- Outcome updates may append or update only the outcome section according to
  the existing Passport terminal-state rules.
- Raw snapshot payloads are never written to ordinary application logs.
- Migration preserves snapshots and returns `UNKNOWN` when a field cannot be
  migrated safely.

The JSON structure is a V1 storage choice, not a license for unvalidated
arbitrary data. Schema validation and maximum-size limits are required.

V1 retention is 365 days from `created_at`, configurable through the Settings
Engine. Snapshot retention must follow the Passport retention policy when that
policy is stricter. A retention job must be auditable, idempotent, and never
delete a live or unresolved snapshot. After deletion, the Passport reports
`NOT_APPLICABLE` or `UNAVAILABLE` rather than attempting reconstruction.

## 7. Capture Pipeline

```text
MT5 closed-candle feed
  -> detector and approved evaluators
  -> AlertEvent / Signal Passport creation
  -> immutable replay snapshot capture
  -> alert delivery
  -> existing outcome tracker
  -> closed-candle outcome context update
```

Capture must occur after the Signal Passport has a stable `signal_id` and
before the alert is considered fully reported. A snapshot failure must not
create a different signal, change confidence, or block a valid alert unless a
future approved policy explicitly says otherwise. The initial policy is to
deliver the signal and mark replay as `UNAVAILABLE`, while logging the failure.

The capture service must receive data through an explicit typed interface. It
must not query MT5 independently in a way that could observe a forming candle
or create a second market-data boundary.

Phase 3 implementation note: the live scanner passes the exact closed
source-timeframe sequence and the detector's stored parent, sweep, and
confirmation candle objects to `ReplayCaptureService`. When an approved
higher-timeframe evaluator was used in the same cycle, its already-fetched
closed context is included after filtering at the formation cutoff. Capture
errors are logged and leave the original alert path unchanged.

Phase 4 implementation note: `ReplayOutcomeService` reads the existing
Signal Passport journal after the tracker runs. It appends only later closed
source-timeframe candles and mirrors validated `tp2_hit`, `sl_hit`, or
`signal_expired` states. Contradictory journal fields become `UNKNOWN`, and a
terminal replay record is never rewritten by a later scan.

## 8. Rendering Design

### 8.1 V1 Static Telegram Image

The renderer must:

- Accept a validated snapshot only.
- Use Matplotlib with the non-interactive `Agg` backend for V1. `mplfinance`,
  Plotly, browsers, and TradingView are not required.
- Render a deterministic 16:9 PNG at 1600 x 900 pixels, with a configurable
  maximum file size of 2 MB.
- Render deterministic candles and price markers.
- Mark the formation boundary clearly.
- Highlight parent, sweep, and confirmation roles with the specified labels
  and outlines.
- Mark entry, stop, and target levels from the stored Passport values.
- Display `UNKNOWN` when the outcome is unresolved.
- Include pair, timeframe, direction, and `Data as of` in the image header.
- Avoid exposing internal configuration values that are not public.
- Bound image dimensions, candle count, and rendering time.
- Return a typed failure rather than a blank or misleading image.

The chosen chart library must be checked against the current runtime before it
is added. The implementation must use an existing approved dependency or add a
reviewed dependency with tests; it must not assume that TradingView, a browser,
or public HTTPS is available.

### 8.2 Telegram Delivery

The Telegram channel should expose a small typed operation such as
`send_signal_replay(snapshot, recipient)`. It must not contain detector or
strategy logic.

Replay delivery must be separately logged from the original signal delivery.
A replay failure must not be reported as a signal failure and must not trigger
duplicate trading alerts.

The Telegram caption is intentionally short and scannable:

```text
📼 EURUSD | H1 | BUY
🕒 Formed: 2026-08-16 12:00 UTC
📌 Status: Resolved | WIN
🧾 Data as of: 2026-08-16 15:00 UTC
```

The caption must not repeat the full rule explanation or raw metadata. The
chart carries the visual detail and the Passport carries the full approved
public summary.

Subscriber replay access is recipient-scoped. At capture time, ATLAX records
only the Telegram chat IDs for which the alert delivery API returned success in
an internal authorization field. The Passport history button is shown only
when that grant exists, and the callback re-checks it before rendering. An
active subscription alone does not authorize access to an arbitrary signal ID;
the admin chat remains an approved operational exception. Older snapshots that
have no delivery grant are unavailable to subscribers rather than being
guessable.

### 8.3 Performance and Failure Handling

Replay generation must not block the scanner or live alert delivery. A replay
request has a configurable render timeout with a V1 default of 5 seconds. If
validation, storage, rendering, or Telegram delivery fails, the bot returns:

`Replay is temporarily unavailable. The original signal is unaffected.`

The failure is logged with a stable reason code and the signal ID. The bot must
not send a blank image, retry indefinitely, or create a duplicate signal.

Rendered images use a bounded derived cache. The cache key is:

```text
SHA256(snapshot_hash + renderer_version + image_format + width + height)
```

The cache is stored under a configurable local cache directory in local mode
and an approved shared object/cache service in multi-instance production. It
is never authoritative and can be safely rebuilt from the snapshot. Repeated
requests for the same key reuse the image; a single-flight lock prevents
duplicate concurrent renders. Cache entries are evicted by age and size, and
cache failures fall back to direct rendering.

## 9. Configuration

All operational values must flow through the Settings Engine:

- Replay enabled flag.
- Formation context window size.
- Outcome context window size.
- Maximum candles per snapshot.
- Maximum serialized snapshot size.
- Maximum render time.
- Chart image dimensions and format.
- Replay access by plan, if plan gating is approved.
- Retention period.

The V1 presentation defaults are:

| Setting | V1 default | Boundary |
| --- | ---: | --- |
| `formation_context_bars_before_parent` | `30` | Must be at least 0; never changes detector input. |
| `outcome_forward_bars` | `50` | Must be at least 0; never changes tracker expiry. |
| `maximum_snapshot_candles` | `200` | Hard safety ceiling for one snapshot. |
| `maximum_snapshot_bytes` | `524288` | 512 KiB serialized snapshot ceiling. |
| `replay_render_timeout_seconds` | `5` | Renderer is cancelled or failed after the limit. |
| `replay_image_width` | `1600` | 16:9 Telegram-friendly output. |
| `replay_image_height` | `900` | 16:9 Telegram-friendly output. |
| `replay_image_format` | `PNG` | Deterministic lossless output. |
| `replay_image_max_bytes` | `2097152` | 2 MiB Telegram delivery ceiling. |
| `replay_retention_days` | `365` | Must not exceed the approved Passport retention policy. |
| `replay_cache_max_age_days` | `30` | Derived cache only; source snapshot is unaffected. |
| `replay_cache_max_size_mb` | `512` | Evict oldest derived images first. |

These defaults require Phase 0 approval and remain configuration values rather
than hidden constants. V1 replay is available to any active subscriber who is
authorized to view the signal. Plan-specific replay depth or premium-only
features require a separate entitlement decision and must not silently reduce
Standard Passport access.

Invalid replay configuration must fail closed for replay capture and preserve
the last known valid configuration. It must not disable the live detector or
change signal routing.

## 10. Access and Privacy

- A subscriber may view only Passport and replay records they are authorized
  to receive.
- Admins may access internal replay evidence according to existing admin
  authorization.
- The replay endpoint must validate the subscriber, `signal_id`, and access
  state before reading the snapshot.
- Replay queries must be rate-limited and audited.
- No raw database access, file access, or arbitrary signal ID lookup is exposed
  to ATLAX AI.
- ATLAX AI may summarize an already authorized replay result, but it must not
  generate a new signal interpretation or claim a future outcome.

## 11. Logging and Monitoring

Add structured events for:

- `signal_replay_capture_started`.
- `signal_replay_capture_succeeded`.
- `signal_replay_capture_failed`.
- `signal_replay_requested`.
- `signal_replay_authorization_denied`.
- `signal_replay_render_succeeded`.
- `signal_replay_render_failed`.
- `signal_replay_delivery_succeeded`.
- `signal_replay_delivery_failed`.
- `signal_replay_unknown`.

Logs must include signal ID, pair, timeframe, bounded status, configuration
snapshot identifiers, and failure reason where safe. Do not log raw candles,
wallets, credentials, broker account data, or full Telegram payloads.

Operations counters should include:

- Capture success count.
- Capture failure count.
- Snapshot unavailable count.
- Replay requests.
- Authorization denials.
- Render failures.
- Telegram replay delivery failures.
- Average and percentile render latency.

## 12. Implementation Phases

### Phase 0 - Contract and Governance

- [ ] Approve this plan.
- [ ] Confirm V1 is closed-candle replay only.
- [ ] Approve context-window and retention configuration values.
- [ ] Confirm whether Standard and Premium receive the same basic replay.
- [ ] Record an ADR stating that replay uses MT5 data and is analytics-only.

Exit criteria: no unresolved behavior is implemented by assumption.

### Phase 1 - Snapshot Model and Validation

- [x] Add typed candle and replay snapshot models.
- [x] Add schema validation and size limits.
- [x] Normalize all snapshot timestamps to UTC.
- [x] Reject forming candles and malformed OHLC values.
- [x] Add canonical hashing and snapshot versioning.

Exit criteria: valid snapshots are deterministic and invalid snapshots return
`UNKNOWN` or a typed validation failure.

### Phase 2 - Local and PostgreSQL Persistence

- [x] Add local storage schema.
- [x] Add PostgreSQL schema and repository adapter.
- [x] Add idempotent insert and terminal-state-safe outcome update.
- [x] Add migration and dual-read verification coverage.
- [x] Add repository failure and rollback tests.

Exit criteria: local and PostgreSQL repositories return equivalent snapshot
records for the same fixtures.

### Phase 3 - Live Capture Integration

- [x] Capture the exact closed candles supplied to the live signal pipeline.
- [x] Link the snapshot to the existing Passport `signal_id`.
- [x] Preserve the original alert path if capture fails.
- [x] Capture approved evaluator context without adding new trading rules.
- [x] Verify restart idempotency and duplicate prevention.

Exit criteria: new signals have replay availability recorded and live signal
delivery behavior is unchanged.

### Phase 4 - Outcome Integration

- [x] Attach final tracker outcomes to the replay record.
- [x] Preserve `UNKNOWN` for expiry and incomplete outcomes.
- [x] Add outcome consistency checks against the Signal Passport.
- [x] Ensure repeated outcome events cannot rewrite terminal results.

Exit criteria: replay outcome and Passport outcome agree in all fixtures.

### Phase 5 - Telegram Replay Renderer

- [x] Select and validate an approved local chart renderer.
- [x] Implement deterministic static image generation.
- [x] Add Passport and Replay buttons to approved subscriber surfaces.
- [x] Enforce recipient authorization and per-chat rate limits.
- [x] Send replay images with customer-safe captions.

Phase 5 implementation note: `ReplayRenderer` uses Matplotlib's `Agg`
backend, produces a cached 16:9 PNG with the exact P/S/C role markers,
parent high/low range, stored entry/stop/target references, outcome marker,
and UTC data-as-of label. Telegram uploads use a bounded multipart
`sendPhoto` request. Replay work is scheduled on a small background executor
so the scanner's live alert and command polling loop is not held by image
generation. Missing renderer dependencies, oversized images, timeout, and
Telegram failures return typed/logged replay errors without changing alerts.

Exit criteria: an authorized subscriber can request a replay and receives a
non-empty chart with correct formation and outcome markers.

### Phase 6 - Reporting and Quality Review

- [x] Add replay availability to Signal Passport history.
- [x] Add aggregate replay coverage metrics.
- [x] Add performance breakdowns by pair, timeframe, style, route, and grade
  only where sample sizes are sufficient.
- [x] Add ATLAX AI read-only explanation support using authorized deterministic
  replay results.
- [x] Document known limitations and data retention.

Phase 6 implementation note: `build_signal_replay_report` uses the Passport
aggregate as its denominator and marks missing or failed storage as `UNKNOWN`.
Its route dimension is non-exclusive when a signal was delivered to both
Standard and Premium subscribers. Grade and route values absent from legacy
snapshots are grouped under `UNKNOWN`. The read-only MCP report is available
for controlled use. On 2026-08-16, the project lead approved adding only this
report's aggregate coverage and outcome buckets to the external ATLAX AI
subscriber prompt. Raw candles, price levels, signal IDs, recipient IDs, and
account or execution data remain excluded. `UNKNOWN` replay status is passed
through as unavailable context rather than converted to zeroes or an inference.

Exit criteria: reporting distinguishes signal quality from replay availability
and does not claim performance from insufficient or unknown data.

Production verification status (2026-08-16): the local replay and ATLAX AI
test suite passes. A controlled live `/ask` request reached Gemini and
returned a Telegram-safe answer. The current journal does not yet contain the
`signal_replay_snapshots` table, so the live replay report correctly returns
`UNKNOWN` until the writable scanner initializes the replay schema. ATLAX AI
must not present that state as zero coverage or infer replay outcomes. No
provider credential belongs in tracked YAML, source code, or the replay
payload.

## 13. Test Plan

### Unit Tests

- Closed-candle validation.
- UTC normalization.
- Snapshot hashing stability.
- Snapshot size limits.
- Exact three-candle role mapping and parent high/low marker placement.
- Missing role metadata returns `UNKNOWN` instead of guessed candles.
- Duplicate capture idempotency.
- Terminal outcome protection.
- Formation versus outcome window separation.
- Public field redaction.
- Renderer output is non-empty and deterministic.

### Contract Tests

- Local and PostgreSQL snapshot repository parity.
- Signal Passport to replay identity linkage.
- Telegram replay request and delivery contract.
- Configuration validation and live reload behavior.

### Security Tests

- Unauthorized subscriber cannot fetch another subscriber's replay.
- Invalid signal IDs fail safely.
- Rate limits are enforced.
- Snapshot payloads and secrets are absent from logs.
- AI receives only authorized sanitized results.

### Regression Tests

- Detector outputs are unchanged.
- Confidence scores are unchanged.
- Signal routing is unchanged.
- Execution and risk paths are unchanged.
- A replay failure does not suppress a valid live alert.
- A restart does not create duplicate snapshots or signals.

## 14. Acceptance Criteria

The feature is complete only when:

1. Every newly captured signal has a stable replay availability state.
2. The formation view contains no future candle data.
3. The post-detection view clearly separates later candles from formation
   evidence.
4. A missing or invalid snapshot is shown as unavailable, never fabricated.
5. Replay does not change detection, confidence, routing, execution, or risk.
6. Signal Passport and replay outcomes remain consistent after restarts.
7. Local and PostgreSQL repositories pass parity tests.
8. Telegram replay access is authorized, rate-limited, and audited.
9. The chart is understandable on a mobile Telegram screen.
10. Old signals are clearly labeled as `NOT_APPLICABLE` when no verified
    snapshot exists.

## 15. Known Limitations

- V1 does not recreate tick-by-tick broker execution.
- V1 does not prove the subscriber's account PnL.
- V1 cannot reconstruct missing historical snapshots with certainty.
- Broker-specific historical data can differ across terminals; the captured
  snapshot is the authoritative replay source for the signal.
- A chart is an explanation aid, not a guarantee or a trading instruction.
