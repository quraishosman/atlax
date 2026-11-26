import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from core.payments.automatic_verification import AutomaticPaymentVerifier
from core.payments.payment_service import PaymentService
from core.payments.payment_store import PaymentStore
from core.alerts.subscriber_db import SubscriberDB
from core.market_data.scanner import MarketScanner
from core.settings.payment_config import (
    CryptoPaymentConfig,
    MpesaPaymentConfig,
    PaymentConfig,
)


TON_DESTINATION = "UQdestination"
TON_MASTER = "EQmaster"
TON_HASH = "ton-transaction-hash"


def _ton_config(**overrides):
    values = {
        "automatic_verification_enabled": True,
        "automatic_verification_shadow_mode": True,
        "verification_timeout_seconds": 5,
        "verification_clock_skew_seconds": 300,
        "ton_verification_api_url": "https://ton.example/transfers",
        "ton_verification_api_key": "",
        "ton_usdt_jetton_master": TON_MASTER,
        "ton_usdt_decimals": 6,
        "usdt_ton": CryptoPaymentConfig(True, TON_DESTINATION, "TON", "USDT"),
        "solana_verification_rpc_url": "",
        "solana_token_mint": "",
        "solana_token_decimals": 6,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _ton_intent(amount="15"):
    return {
        "method": "usdt_ton",
        "amount_usd": amount,
        "submitted_tx_reference": TON_HASH,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _ton_payload(**overrides):
    transfer = {
        "transaction_hash": TON_HASH,
        "transaction_aborted": False,
        "jetton_master": TON_MASTER,
        "destination": TON_DESTINATION,
        "amount": "15000000",
        "transaction_now": int(datetime.now(timezone.utc).timestamp()),
    }
    transfer.update(overrides)
    return {"jetton_transfers": [transfer]}


def test_ton_verifier_accepts_allowlisted_exact_transfer():
    calls = []

    def get_json(url, headers):
        calls.append((url, headers))
        return _ton_payload()

    verifier = AutomaticPaymentVerifier(_ton_config(), get_json=get_json)
    result = verifier.verify(_ton_intent())

    assert result.status == "verified"
    assert result.network == "TON"
    assert result.observed_amount == Decimal("15")
    assert "jetton_master=EQmaster" in calls[0][0]


def test_ton_verifier_rejects_wrong_master_destination_or_amount():
    for override, expected_reason in [
        ({"jetton_master": "EQfake"}, "master"),
        ({"destination": "UQother"}, "destination"),
        ({"amount": "14999999"}, "amount"),
        ({"transaction_aborted": True}, "aborted"),
    ]:
        verifier = AutomaticPaymentVerifier(
            _ton_config(),
            get_json=lambda url, headers, override=override: _ton_payload(**override),
        )
        result = verifier.verify(_ton_intent())
        assert result.status == "rejected"
        assert expected_reason in result.reason.lower()


def test_ton_verifier_rejects_real_transfer_from_before_checkout():
    intent = _ton_intent()
    intent["created_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).isoformat()
    verifier = AutomaticPaymentVerifier(
        _ton_config(),
        get_json=lambda url, headers: _ton_payload(
            transaction_now=int((datetime.now(timezone.utc) - timedelta(minutes=20)).timestamp())
        ),
    )

    result = verifier.verify(intent)

    assert result.status == "rejected"
    assert "predates" in result.reason.lower()


def test_ton_verifier_fails_closed_when_provider_or_configuration_is_missing():
    missing_config = AutomaticPaymentVerifier(
        _ton_config(ton_usdt_jetton_master=""),
    )
    assert missing_config.verify(_ton_intent()).status == "pending"

    provider_error = AutomaticPaymentVerifier(
        _ton_config(),
        get_json=lambda url, headers: (_ for _ in ()).throw(TimeoutError("offline")),
    )
    assert provider_error.verify(_ton_intent()).status == "unknown"


def test_solana_verifier_accepts_exact_spl_token_balance_delta():
    destination = "SolanaDestination"
    mint = "UsdcMint"
    payload = {
        "result": {
            "blockTime": int(datetime.now(timezone.utc).timestamp()),
            "transaction": {
                "message": {
                    "accountKeys": [{"pubkey": "TokenAccount"}],
                    "instructions": [
                        {
                            "program": "spl-token",
                            "parsed": {
                                "type": "transferChecked",
                                "info": {
                                    "destination": "TokenAccount",
                                    "mint": mint,
                                    "tokenAmount": {"amount": "15000000"},
                                },
                            },
                        }
                    ],
                }
            },
            "meta": {
                "err": None,
                "preTokenBalances": [
                    {
                        "accountIndex": 0,
                        "mint": mint,
                        "owner": destination,
                        "uiTokenAmount": {"amount": "0"},
                    }
                ],
                "postTokenBalances": [
                    {
                        "accountIndex": 0,
                        "mint": mint,
                        "owner": destination,
                        "uiTokenAmount": {"amount": "15000000"},
                    }
                ],
            }
        }
    }
    verifier = AutomaticPaymentVerifier(
        _ton_config(
            solana_verification_rpc_url="https://solana.example",
            solana_token_mint=mint,
            solana=CryptoPaymentConfig(True, destination, "Solana", "USDC"),
        ),
        post_json=lambda url, body, headers: payload,
    )
    result = verifier.verify(
        {
            "method": "solana",
            "amount_usd": "15",
            "submitted_tx_reference": "1" * 64,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    assert result.status == "verified"
    assert result.observed_amount == Decimal("15")


def test_solana_verifier_rejects_balance_delta_without_transfer_to_atlax():
    destination = "SolanaDestination"
    mint = "UsdcMint"
    payload = {
        "result": {
            "blockTime": int(datetime.now(timezone.utc).timestamp()),
            "transaction": {
                "message": {
                    "accountKeys": [{"pubkey": "TokenAccount"}],
                    "instructions": [
                        {
                            "program": "spl-token",
                            "parsed": {
                                "type": "transferChecked",
                                "info": {
                                    "destination": "OtherTokenAccount",
                                    "mint": mint,
                                    "tokenAmount": {"amount": "15000000"},
                                },
                            },
                        }
                    ],
                }
            },
            "meta": {
                "err": None,
                "preTokenBalances": [
                    {
                        "accountIndex": 0,
                        "mint": mint,
                        "owner": destination,
                        "uiTokenAmount": {"amount": "0"},
                    }
                ],
                "postTokenBalances": [
                    {
                        "accountIndex": 0,
                        "mint": mint,
                        "owner": destination,
                        "uiTokenAmount": {"amount": "15000000"},
                    }
                ],
            },
        }
    }
    verifier = AutomaticPaymentVerifier(
        _ton_config(
            solana_verification_rpc_url="https://solana.example",
            solana_token_mint=mint,
            solana=CryptoPaymentConfig(True, destination, "Solana", "USDC"),
        ),
        post_json=lambda url, body, headers: copy.deepcopy(payload),
    )

    result = verifier.verify(
        {
            "method": "solana",
            "amount_usd": "15",
            "submitted_tx_reference": "1" * 64,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    assert result.status == "rejected"
    assert "did not contain" in result.reason.lower()


def test_solana_verifier_rejects_malformed_signature_before_rpc_call():
    calls = []
    verifier = AutomaticPaymentVerifier(
        _ton_config(
            solana_verification_rpc_url="https://solana.example",
            solana_token_mint="UsdcMint",
            solana=CryptoPaymentConfig(True, "SolanaDestination", "Solana", "USDC"),
        ),
        post_json=lambda url, body, headers: calls.append(body),
    )

    result = verifier.verify({
        "method": "solana",
        "amount_usd": "15",
        "submitted_tx_reference": "not-a-solana-signature",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    assert result.status == "rejected"
    assert "format is invalid" in result.reason.lower()
    assert calls == []


def test_payment_service_rejects_reusing_a_transaction_proof(tmp_path):
    config = PaymentConfig(
        enabled=True,
        subscription_price_usd=Decimal("15"),
        premium_subscription_price_usd=Decimal("50"),
        subscription_duration_days=30,
        default_style="day_trader",
        manual_review_required=True,
        payment_reference_prefix="ATLAX",
        payment_ttl_hours=1,
        support_contact="@support",
        crypto=CryptoPaymentConfig(True, "legacy", "Solana", "USDC"),
        mpesa=MpesaPaymentConfig(True, "563563", "ATLAX15", "", ""),
    )
    db = SubscriberDB(
        codes_file=str(tmp_path / "codes.json"),
        subs_file=str(tmp_path / "subs.json"),
    )
    service = PaymentService(
        config,
        store=PaymentStore(str(tmp_path / "payments.json")),
        subscriber_db=db,
    )
    first, _ = service.create_payment_intent("1", "first", "crypto")
    second, _ = service.create_payment_intent("2", "second", "crypto")

    submitted, _ = service.submit_payment(first["reference"], "same-proof")
    duplicate, message = service.submit_payment(second["reference"], "SAME-PROOF")

    assert submitted is not None
    assert duplicate is None
    assert "already been used" in message


def test_payment_service_rejects_proof_from_closed_intent(tmp_path):
    config = PaymentConfig(
        enabled=True,
        subscription_price_usd=Decimal("15"),
        premium_subscription_price_usd=Decimal("50"),
        subscription_duration_days=30,
        default_style="day_trader",
        manual_review_required=True,
        payment_reference_prefix="ATLAX",
        payment_ttl_hours=1,
        support_contact="@support",
        crypto=CryptoPaymentConfig(True, "legacy", "Solana", "USDC"),
        mpesa=MpesaPaymentConfig(True, "563563", "ATLAX15", "", ""),
    )
    db = SubscriberDB(
        codes_file=str(tmp_path / "codes.json"),
        subs_file=str(tmp_path / "subs.json"),
    )
    service = PaymentService(
        config,
        store=PaymentStore(str(tmp_path / "payments.json")),
        subscriber_db=db,
    )
    first, _ = service.create_payment_intent("1", "first", "crypto")
    submitted, _ = service.submit_payment(first["reference"], "closed-proof")
    service.confirm_payment(first["reference"], confirmed_by="admin")
    service.void_payment(first["reference"], voided_by="system", reason="test cleanup")
    assert submitted["status"] == "submitted"

    second, _ = service.create_payment_intent("2", "second", "crypto")
    duplicate, message = service.submit_payment(second["reference"], "CLOSED-PROOF")

    assert duplicate is None
    assert "already been used" in message


def test_admin_crypto_approval_cannot_override_unknown_chain_result():
    scanner = MarketScanner.__new__(MarketScanner)
    scanner._payment_verifier = SimpleNamespace(
        enabled=True,
        verify=lambda intent: SimpleNamespace(
            status="unknown",
            network="TON",
            reason="provider unavailable",
        ),
    )
    audits = []
    scanner._record_operational_audit = lambda *args, **kwargs: audits.append((args, kwargs))

    allowed, result = scanner._verify_crypto_for_admin_approval(
        {
            "method": "usdt_ton",
            "reference": "ATLAX-CR-ABC123",
            "chat_id": "123",
        }
    )

    assert allowed is False
    assert result.status == "unknown"
    assert audits[0][1]["result"] == "unknown"


def test_admin_crypto_approval_allows_only_verified_chain_result():
    scanner = MarketScanner.__new__(MarketScanner)
    scanner._payment_verifier = SimpleNamespace(
        enabled=True,
        verify=lambda intent: SimpleNamespace(
            status="verified",
            network="TON",
            reason="exact transfer matched",
        ),
    )
    scanner._record_operational_audit = lambda *args, **kwargs: None

    allowed, result = scanner._verify_crypto_for_admin_approval(
        {"method": "usdt_ton", "reference": "ATLAX-CR-ABC123", "chat_id": "123"}
    )

    assert allowed is True
    assert result.status == "verified"


def test_void_payment_removes_access_and_reclassifies_confirmed_intent(tmp_path):
    config = PaymentConfig(
        enabled=True,
        subscription_price_usd=Decimal("15"),
        premium_subscription_price_usd=Decimal("50"),
        subscription_duration_days=30,
        default_style="day_trader",
        manual_review_required=True,
        payment_reference_prefix="ATLAX",
        payment_ttl_hours=1,
        support_contact="@support",
        crypto=CryptoPaymentConfig(True, "legacy", "Solana", "USDC"),
        mpesa=MpesaPaymentConfig(True, "563563", "ATLAX15", "", ""),
    )
    db = SubscriberDB(
        codes_file=str(tmp_path / "codes.json"),
        subs_file=str(tmp_path / "subs.json"),
    )
    service = PaymentService(
        config,
        store=PaymentStore(str(tmp_path / "payments.json")),
        subscriber_db=db,
    )
    intent, _ = service.create_payment_intent("99", "tester", "crypto")
    confirmed, _ = service.confirm_payment(intent["reference"], confirmed_by="admin")
    assert confirmed is not None
    assert db.is_registered("99") is True

    voided, message = service.void_payment(
        intent["reference"],
        voided_by="system",
        reason="verification failed",
    )

    assert voided["status"] == "voided"
    assert "deactivated" in message
    assert db.is_registered("99") is False
