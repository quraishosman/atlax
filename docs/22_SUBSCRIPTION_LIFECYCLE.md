# ATLAX Subscription Lifecycle Specification

Status: Active
Version: 1.0

## Purpose

This document defines how ATLAX creates, reviews, activates, reminds, expires,
and audits paid Telegram access. It governs subscriber access only. It does
not define market detection, strategy behavior, risk controls, or execution.

## Core Rules

- Payment proof is never approved automatically by the lifecycle worker.
- Access is activated only after an approved payment-review path succeeds.
- Access is suspended when a finite subscription reaches its expiry timestamp.
- Suspended users keep their profile, preferences, payment history, and Signal
  Passport history.
- Inactive users must not receive subscriber signals or ATLAX AI access.
- Reminder delivery is idempotent across scanner restarts.
- All lifecycle timing is UTC and comes from validated configuration.
- Every system transition and delivery result is logged without secrets.

## Plan Selection

Telegram presents `Standard Plan` and `Premium Plan` before showing payment
rails. Selecting a plan only records the user's checkout choice in a pending
payment intent; it does not activate access or grant premium routing.

The user then chooses an enabled payment rail, submits the payment reference
and transaction proof, and waits for the reviewed confirmation path. The
confirmed intent's `subscription_plan` is copied to the subscriber record only
after approval. Expiry and suspension remove access to the selected plan.

The current plans use separate configured prices and the configured duration:
Standard is `$15 USD` and Premium is `$50 USD` for 30 days. Each payment
intent stores the selected amount and plan for review and revenue reporting.

## Subscriber Alert Labels

After all access, confidence, symbol, timeframe, and plan-entitlement checks
pass, each Telegram alert begins with a public route label:

- `PREMIUM SIGNAL` identifies an alert delivered through the active Premium
  route.
- `STANDARD SIGNAL` identifies an alert delivered through the active Standard
  route.

These labels are informational. They do not expose ATR grades, internal
thresholds, or routing implementation details. The label is added only to the
subscriber copy that is actually sent, and successful delivery logs record the
same route for operational verification.

Subscribers may enable the optional `Key-Level Only` preference. When enabled,
delivery requires `parent_at_htf_zone=true`; unknown key-level context fails
closed for that preference. When disabled, both `HTF KEY-LEVEL` and
`OPEN-STRUCTURE` alerts remain eligible under the subscriber's normal plan,
confidence, symbol, and timeframe filters.

`Premium Confluence` is a Premium-plan entitlement. The Telegram settings menu
must not expose its controls to Standard users, and direct callbacks or command
attempts must fail closed. A persisted preference is not an entitlement: a
Standard or expired subscriber must not receive the combined Premium route even
if an old `confluence_only` value remains in storage.

The preference consumes verified detector metadata; it does not guess a
support, resistance, supply, demand, or other level from the Telegram layer.
An upstream key-level evaluator or webhook must populate that metadata before
`Key-Level Only` can admit an alert.

## State Flow

    awaiting_payment
        -> submitted
        -> confirmed
        -> subscriber active
        -> expiry reminder
        -> subscription expired
        -> subscriber inactive

Other terminal payment states are rejected and expired. A rejected or expired
payment intent cannot activate access. A confirmed renewal creates a new
subscription cycle and resets the reminder markers for that cycle.

## Maintenance Pass

The scanner runs one lifecycle maintenance pass per configured poll cycle.
The pass also runs while market scanning is paused. It:

1. Expires open payment intents whose payment TTL has elapsed.
2. Suspends active subscriptions whose expiry has been reached.
3. Sends any due expiry notice after access suspension.
4. Sends at most one due pre-expiry reminder per subscriber in that pass.
5. Records the result counts in structured logs.

If the scanner is stopped, the next startup catches up from the persisted
timestamps. No payment is confirmed merely because the scanner was offline.

## Reminder Policy

The configured reminder list is expressed in hours before expiry. The
operational default is:

    payments:
      expiry_reminder_hours: [72, 24]

Use ATLAX_SUBSCRIPTION_EXPIRY_REMINDER_HOURS=72,24 to override it. Set an
empty YAML list to disable pre-expiry reminders. The smallest due threshold is
selected when several thresholds are overdue, which prevents a restart from
sending a burst of old reminders. A threshold is marked sent only after
Telegram accepts the message.

Each paid activation or renewal resets the markers:

    {
      "subscription_reminders_sent": {},
      "subscription_expiry_notice_sent": false
    }

## Access Suspension

At expiry, the subscriber record is updated with:

- active: false
- subscription_status: "expired"
- subscription_expired_at
- subscription_expiry_notice_sent: false

The existing subscriber access gates enforce the result. No subscriber record
is deleted, and no trading or performance record is changed.

## Payment and Approval Audit

Each payment intent keeps an append-only status_history with status, timestamp,
actor when applicable, and a short note. The operational audit log records:

- payment intent expiry
- admin payment queue and revenue views
- approval and rejection actions
- subscription access suspension
- expiry notice delivery
- expiry reminder delivery and failures

Payment destinations, wallet credentials, transaction secrets, and private
keys must never be written to lifecycle audit metadata.

## Admin Revenue Summary

The admin revenue view reports:

- confirmed revenue for the last configurable report window, default 30 days
- confirmed payment count
- open payment requests
- current ledger counts by status
- all-time confirmed count and gross amount
- confirmed totals grouped by payment rail for the report window

Only confirmed payment intents contribute to revenue. Awaiting, submitted,
rejected, and expired intents never count as revenue.

The Telegram admin interfaces are /revenue and the Revenue Summary button in
/admin. The existing payment queue remains the approval surface.

## Failure Behavior

- A failed reminder delivery does not mark the reminder as sent.
- A failed expiry notice is retried on a later maintenance pass.
- A lifecycle exception is logged and must not abort market scanning.
- Missing or invalid configuration fails during configuration loading.
- No lifecycle operation grants access when the required state is ambiguous.

