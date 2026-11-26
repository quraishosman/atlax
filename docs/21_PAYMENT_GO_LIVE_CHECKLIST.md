# ATLAX Payment Go-Live Checklist

Status: LIVE CONFIGURED — one real funded smoke payment remains pending
Owner: ATLAX Engineering

This checklist is the operational handoff for customer subscription payments.
It records what is enabled, what is enforced, and what must be observed after
the first real customer payment.

## Customer Offers

- Standard Plan: 15 USD for 30 days.
- Premium Plan: 50 USD for 30 days.
- USDC on Solana: automatic on-chain verification.
- M-Pesa Lipa Namba: administrator verification.
- Payment references: `ATLAX-CR-XXXXXX`, valid for one hour.

## Live Configuration

- `payments.enabled`: enabled.
- `ATLAX_AUTOMATIC_VERIFICATION_ENABLED`: `true`.
- `ATLAX_AUTOMATIC_VERIFICATION_SHADOW_MODE`: `false`.
- Solana RPC, USDC mint, and ATLAX destination wallet: configured through
  environment variables only.
- Payment destinations and provider credentials are not stored in source
  control.

## Automatic Solana Acceptance Rules

ATLAX accepts a Solana payment only when every condition passes:

1. The submitted signature has valid Solana base58/64-byte format.
2. The transaction is found at finalized commitment.
3. The transaction has no execution error.
4. The transaction timestamp is within the payment-reference window.
5. The parsed instruction is an SPL `transfer` or `transferChecked`.
6. The instruction uses the configured USDC mint and exact plan amount.
7. The instruction destination token account maps to a token account owned by
   the configured ATLAX wallet.
8. The signature has not already been used by another payment reference.

A balance change without the matching transfer instruction is rejected.
Provider errors, missing data, and unfinalized transactions do not activate
access. Administrator approval cannot override an unverified crypto proof
while automatic verification is enabled.

## Completed Verification

- Focused payment and lifecycle tests pass: `19 passed`.
- Correct-wallet USDC transfer is covered by a positive test.
- A fake balance delta with a different transfer destination is covered by a
  rejection test.
- Invalid signatures are rejected before an RPC request.
- Reused signatures are rejected globally, including previously closed
  payment intents.
- Old transactions are rejected when they predate the payment reference.
- Failed, wrong-mint, wrong-destination, and wrong-amount proofs fail closed.
- The live scanner restarted successfully and remains a single running
  instance.

## Pending Real-World Check

- [ ] Submit one small real USDC payment using a newly generated reference.
- [ ] Confirm the transaction is auto-verified without admin approval.
- [ ] Confirm the selected Standard or Premium access is activated.
- [ ] Confirm the user receives the activation message and correct alert route.
- [ ] Confirm a second submission of the same signature is rejected as reused.

Until this smoke payment is completed, the implementation is test-verified
and live-configured, but the external RPC-to-wallet path has not received a
fresh end-to-end confirmation in production.

## Operations After Launch

- Review `payment_verification_result` audit events daily.
- Investigate `unknown` results as provider or configuration incidents.
- Never approve an unverified crypto proof manually while automatic
  verification is enabled.
- Rotate the RPC key if it is exposed, and never place it in Telegram,
  source control, or logs.
- Keep M-Pesa payments in the administrator-review path until a separate
  trusted provider integration is approved.
