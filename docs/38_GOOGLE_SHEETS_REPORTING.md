# Google Sheets Reporting

Version: 1.0  
Status: ACTIVE  
Purpose: Mirror ATLAX signal, payment, and subscriber lifecycle data into one
private Google Spreadsheet for operational reporting.

## Architecture

ATLAX uses one Apps Script Web App and one `doPost` router. The router writes to
separate worksheet tabs:

| Tab | Purpose |
| --- | --- |
| `Signals All` | One row for every accepted ATLAX signal |
| `Standard Signals` | Only signals delivered to Standard subscribers |
| `Premium Signals` | Only signals delivered to Premium subscribers |
| `Grade Performance` | A+, A, B, C, and UNKNOWN counts and outcomes by route |
| `Payments` | Current payment state, user, plan, rail, and reference |
| `Payment Events` | Append-only payment lifecycle history |
| `Subscribers` | Current subscriber profile and expiry state |
| `Subscriber Events` | Append-only subscriber lifecycle history |
| `Event Log` | Idempotency record for received events |

The Standard and Premium signal tabs are intentionally separate. If the same
signal reaches both plans, it appears once in each applicable tab and keeps the
same `correlation_id` for outcome reconciliation.

## Data Ownership

The ATLAX journal, Signal Passport, payment store, and subscriber store remain
authoritative. Google Sheets is a reporting mirror. A webhook failure must not
change signal detection, Telegram delivery, payment verification, subscription
activation, risk, or execution.

## Event Contract

The Python reporter in `core/analytics/google_sheets_reporting.py` sends these
events:

```text
signal_formed
signal_delivered
signal_resolved
payment_created
payment_submitted
payment_confirmed
payment_rejected
payment_voided
payment_expired
subscriber_created
subscriber_activated
subscriber_expired
```

Every event includes:

```text
schema_version: 3.0
event_type
event_id       stable idempotency key
correlation_id signal ID, payment reference, or Telegram chat ID
occurred_at
signature      HMAC derived from event identity
```

`signal_id` is kept as the internal `correlation_id` rather than displayed as a
prominent customer-facing column. It is required to join a formed signal to
its later outcome and to update both plan-specific tabs correctly.

## Performance Rules

`Grade Performance` includes `A+` because ATLAX premium routing supports A/A+
quality. The user-facing comparison should use:

```text
win_rate = wins / (wins + losses)
```

Open, expired, and UNKNOWN outcomes are excluded from the denominator. Always
review the resolved sample count alongside the percentage; a high percentage
with a tiny sample is not reliable evidence.

## Deployment

1. Open the private Google Spreadsheet and open **Extensions -> Apps Script**.
2. Replace the project code with [`GOOGLE_SHEETS_REPORTING.gs`](C:/Users/HP/Desktop/Atlax/docs/GOOGLE_SHEETS_REPORTING.gs).
3. In Apps Script **Project Settings -> Script Properties**, create:
   `ATLAX_REPORTING_SHARED_SECRET` with a newly generated random value.
4. Set the same value locally as `ATLAX_GOOGLE_SHEETS_SHARED_SECRET`.
5. Run `setupSheets()` once from the Apps Script editor and authorize the
   spreadsheet access.
6. Deploy as a Web App. Keep the deployed URL only in the runtime environment
   variable `ATLAX_GOOGLE_SHEETS_WEBHOOK_URL`.
7. Send a test event and confirm that `Standard Signals`, `Premium Signals`,
   `Payments`, and `Subscribers` are separate tabs.

The shared secret must never be committed to Git, written to a worksheet, or
printed in logs. ATLAX sends only the derived HMAC signature. Do not include
bot tokens, broker credentials, wallet private keys, passwords, or account
secrets in any reporting event.

## Verification

- Repeat the same event and confirm `Event Log` prevents a duplicate row.
- Resolve a signal delivered to Standard and Premium and confirm both tabs
  update the same outcome.
- Create and confirm a payment and confirm `Payments` is updated while
  `Payment Events` retains the lifecycle history.
- Activate a subscriber and confirm `Subscribers` is updated while
  `Subscriber Events` receives the activation event.
- Temporarily make the webhook unavailable and confirm ATLAX continues its
  normal trading and subscription behavior while logging the reporting error.
