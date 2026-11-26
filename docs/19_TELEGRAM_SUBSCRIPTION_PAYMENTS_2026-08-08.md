# ATLAX Telegram Subscription Payments

Date: 2026-08-08  
Status: Active

This document is the handoff note for the Telegram subscription payment flow.

## What It Does

- Presents a Telegram purchase flow for ATLAX subscriptions.
- Supports named payment rails:
  - USDT on TON through a Web3 wallet address.
  - Solana (SOL).
  - Vodacom M-Pesa Lipa kwa M-Pesa instructions using a merchant Lipa Namba.
  - Card checkout only when a secure provider URL is configured.
- Creates a payment intent for every purchase request.
- Stores payment intents in `data/payment_intents.json`.
- Activates subscriber access only after confirmation.

## Automatic Crypto Verification

ATLAX can perform a read-only chain check after `/paid REF TRANSACTION_ID` is
submitted. The check never signs transactions, uses private keys, trusts a
user-supplied token name, or activates access from a provider error.

The repository default is disabled with shadow mode enabled. In shadow mode,
ATLAX records whether a crypto proof would pass but keeps the existing admin
review flow. Live activation requires both explicit enablement and
`ATLAX_AUTOMATIC_VERIFICATION_SHADOW_MODE=false`.

For USDT on TON, ATLAX checks the submitted transaction hash, successful
transaction status, configured Jetton master, configured destination wallet,
and exact plan amount in base units. For Solana, ATLAX checks a finalized
transaction, the exact SPL-token mint and amount, and a parsed `transfer` or
`transferChecked` instruction whose destination token account resolves through
the transaction account keys to a token account owned by the configured ATLAX
wallet. A matching balance change without that explicit transfer is rejected.
M-Pesa remains administrator-verified.

Every verification attempt is logged and audited as `verified`, `pending`,
`unknown`, `rejected`, or `disabled`. A transaction proof cannot be reused
against another payment reference. When automatic verification is enabled,
administrator approval cannot override an unverified crypto proof. This keeps
shadow mode from granting access during a provider outage: shadow mode delays
automatic activation, but a crypto proof still needs a `verified` chain result
before an administrator can approve it.

## Subscription Policy

- Default subscription price: 15 USD.
- Default duration: 30 days.
- Default routing style: `day_trader`.
- Payment proof is collected with the `/paid REF TRANSACTION_ID_OR_SIGNATURE` command.
- Admin confirmation is handled through Telegram callback buttons.
- Payment references expire after one hour by default. This is controlled by
  `ATLAX_PAYMENT_TTL_HOURS` and must remain positive.
- M-Pesa requires `ATLAX_MPESA_LIPA_NUMBER`. `ATLAX_MPESA_AMOUNT_TZS` is
  optional; when it is empty, the customer pays the current TZS equivalent of
  the selected USD plan after confirming the amount with ATLAX. ATLAX does
  not invent an exchange rate.
- Crypto destinations are environment-only and use the named variables listed
  below. ATLAX displays the configured network and asset before the user sends
  funds.

## Files Changed

- `core/settings/payment_config.py`
- `core/payments/payment_store.py`
- `core/payments/payment_service.py`
- `core/alerts/subscriber_db.py`
- `core/market_data/scanner.py`
- `run_scanner.py`
- `config/atlax.yaml`
- `.env.example`
- `docs/13_CONFIGURATION.md`
- `docs/decisions/ADR-004.md`

## Runtime Data

- `data/subscribers.json` stores subscriber access state.
- `data/payment_intents.json` stores pending and confirmed payment attempts.
- `data/invite_codes.json` remains available for admin-issued complimentary access.

## Operational Commands

- `/subscribe` or `/buy` opens the payment menu.
- `/buy usdt_ton`, `/buy solana`, and `/buy card` open their instruction
  previews.
- `/buy crypto` remains a legacy shortcut that creates the Solana-compatible
  payment request directly; `/buy mpesa` remains available for compatibility.
- `/paid REF TX_ID` submits payment proof.
- `/status` shows the latest access state.
- Admin callback buttons approve or reject submitted payment proofs.

## Customer Payment Flow

1. `/subscribe` opens Standard and Premium plan cards.
2. The customer selects a plan and then a payment rail.
3. ATLAX shows the rail instructions without creating a payment reference.
4. `🔑 Generate Payment Reference` creates or reuses one open intent for that
   customer, method, and plan. All checkout references use the approved
   `ATLAX-CR-XXXXXX` format; the stored method field identifies the rail.
5. `📤 Submit Payment Proof` shows the exact `/paid` command for that reference.
6. The admin reviews the submitted proof and approves or rejects it from the
   Telegram payment queue.
7. Approval activates the selected plan for 30 days and notifies the customer.

The store enforces reference uniqueness globally and reuses an open reference
for the same customer, rail, and plan. This prevents repeated button taps from
creating a pile of active references.

## Customer Payment Instructions

The subscription menu exposes the configured named rails. Selecting a rail
opens its payment detail screen first. The reference is created only after the
customer presses `Generate Payment Reference`. The generated screen shows the
configured wallet or Lipa Namba, network, asset, amount, expiry, and proof
submission format.

The buttons reflect operator-enabled rails so the checkout choices remain
visible during setup. ATLAX still fails closed and refuses to create a payment
intent until the selected rail has every required destination value configured.

For Vodacom M-Pesa, the bot guides the customer through `*150*00#`, `4 — Lipa
kwa M-Pesa`, `1 — Weka Lipa Namba`, the configured Lipa Namba, the configured
TZS amount, PIN entry, and confirmation. Airtel Money users are shown the
`*150*60#` path through `5 — Lipa bili`, `1 — Lipa kwa simu mitandao yote`,
`1 — Lipa kwa Vodacom M-Pesa`, then the merchant/Lipa-number entry. Halotel
users are directed to pay through the Vodacom M-Pesa Lipa Namba shown by the
bot and verify the merchant details.

The `📤 Submit Payment Proof` button is tied to the payment reference that was
created for that checkout attempt. Customers submit the reference together
with the matching transaction ID or Solana transaction signature using
`/paid REF TX_ID_OR_SIGNATURE`. They must never send a M-Pesa PIN, wallet seed
phrase, private key, or account password. The reference alone is not sufficient
for verification.

For crypto, the bot displays the configured wallet address and requires the
matching network transaction ID or Solana transaction signature. Customers must
verify the asset, network, and destination address before sending. ATLAX never
requests a seed phrase or private key.

## Environment Variables

Wallet destinations and payment provider details remain outside source control:

- `ATLAX_USDT_TON_WALLET_ADDRESS`
- `ATLAX_SOLANA_WALLET_ADDRESS` (falls back to legacy `ATLAX_CRYPTO_WALLET_ADDRESS`)
- `ATLAX_MPESA_LIPA_NUMBER`
- `ATLAX_MPESA_AMOUNT_TZS`
- `ATLAX_CARD_ENABLED` and `ATLAX_CARD_CHECKOUT_URL`

Card payment is fail-closed: no card intent is created until a secure checkout
URL is configured. ATLAX does not collect card numbers, PINs, or one-time codes
in Telegram.

## Security Notes

- The bot must not self-register paid users without confirmation.
- Payment secrets must remain out of source control.
- If a future provider webhook is added, it should confirm payment intents, not bypass them.
