"""Fail-closed verification of submitted crypto payment proofs.

The verifier only reads public chain data. It never signs transactions, holds
private keys, or grants access by trusting a token name supplied by a user.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger("atlax.payments.verification")
_SOLANA_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


@dataclass(frozen=True)
class VerificationResult:
    status: str
    reason: str
    network: str = ""
    transaction_reference: str = ""
    observed_amount: Optional[Decimal] = None


JsonGetter = Callable[[str, dict[str, str]], dict[str, Any]]
JsonPoster = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]]


class AutomaticPaymentVerifier:
    """Verify TON Jetton and configured Solana SPL payment proofs."""

    def __init__(
        self,
        config: Any,
        *,
        get_json: Optional[JsonGetter] = None,
        post_json: Optional[JsonPoster] = None,
    ) -> None:
        self._config = config
        self._get_json = get_json or self._default_get_json
        self._post_json = post_json or self._default_post_json

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._config, "automatic_verification_enabled", False))

    @property
    def shadow_mode(self) -> bool:
        return bool(getattr(self._config, "automatic_verification_shadow_mode", True))

    def verify(self, intent: dict[str, Any]) -> VerificationResult:
        if not self.enabled:
            return VerificationResult("disabled", "automatic verification is disabled")

        method = str(intent.get("method", "")).strip().lower()
        if method == "usdt_ton":
            return self._verify_ton(intent)
        if method in {"solana", "crypto"}:
            return self._verify_solana(intent)
        return VerificationResult("disabled", f"no automatic verifier for {method or 'unknown'}")

    def _verify_ton(self, intent: dict[str, Any]) -> VerificationResult:
        api_url = str(getattr(self._config, "ton_verification_api_url", "") or "").strip()
        api_key = str(getattr(self._config, "ton_verification_api_key", "") or "").strip()
        jetton_master = str(getattr(self._config, "ton_usdt_jetton_master", "") or "").strip()
        destination = str(
            getattr(getattr(self._config, "usdt_ton", None), "wallet_address", "") or ""
        ).strip()
        tx_reference = str(intent.get("submitted_tx_reference", "")).strip()
        if not api_url or not jetton_master or not destination:
            return VerificationResult("pending", "TON verifier is missing API, master, or destination configuration", "TON", tx_reference)
        if not tx_reference:
            return VerificationResult("pending", "transaction hash is missing", "TON")

        query = urlencode(
            [
                ("owner_address", destination),
                ("jetton_master", jetton_master),
                ("limit", "100"),
                ("sort", "desc"),
            ]
        )
        try:
            payload = self._get_json(
                f"{api_url}?{query}",
                {"X-API-Key": api_key} if api_key else {},
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            logger.warning(
                '{"event":"payment_verification_provider_error","network":"TON","error":"%s"}',
                str(exc)[:160],
            )
            return VerificationResult("unknown", "TON provider could not be reached", "TON", tx_reference)

        transfers = payload.get("jetton_transfers", [])
        if not isinstance(transfers, list):
            return VerificationResult("unknown", "TON provider returned an invalid transfer list", "TON", tx_reference)

        matching = [
            item for item in transfers
            if isinstance(item, dict)
            and self._same_value(item.get("transaction_hash"), tx_reference)
        ]
        if not matching:
            return VerificationResult("pending", "TON transaction was not found for the configured wallet", "TON", tx_reference)

        expected = self._expected_decimal(intent)
        decimals = int(getattr(self._config, "ton_usdt_decimals", 6) or 6)
        expected_base_units = expected * (Decimal(10) ** decimals)
        for transfer in matching:
            if transfer.get("transaction_aborted") is True:
                return VerificationResult("rejected", "TON transaction was aborted", "TON", tx_reference)
            if not self._same_value(transfer.get("jetton_master"), jetton_master):
                return VerificationResult("rejected", "TON Jetton master did not match the allowlist", "TON", tx_reference)
            if not self._same_value(transfer.get("destination"), destination):
                return VerificationResult("rejected", "TON transfer destination did not match ATLAX", "TON", tx_reference)
            timestamp_result = self._transaction_time_result(
                intent,
                transfer.get("transaction_now"),
                "TON",
                tx_reference,
            )
            if timestamp_result is not None:
                return timestamp_result
            try:
                observed_base_units = Decimal(str(transfer.get("amount", "")))
            except (InvalidOperation, TypeError, ValueError):
                return VerificationResult("rejected", "TON transfer amount was invalid", "TON", tx_reference)
            if observed_base_units != expected_base_units:
                observed = observed_base_units / (Decimal(10) ** decimals)
                return VerificationResult("rejected", "TON transfer amount did not match the plan", "TON", tx_reference, observed)
            return VerificationResult("verified", "TON USDT transfer matched", "TON", tx_reference, expected)

        return VerificationResult("pending", "TON transfer requires another confirmation check", "TON", tx_reference)

    def _verify_solana(self, intent: dict[str, Any]) -> VerificationResult:
        rpc_url = str(getattr(self._config, "solana_verification_rpc_url", "") or "").strip()
        mint = str(getattr(self._config, "solana_token_mint", "") or "").strip()
        destination = str(
            getattr(getattr(self._config, "solana", None), "wallet_address", "") or ""
        ).strip()
        tx_reference = str(intent.get("submitted_tx_reference", "")).strip()
        if not rpc_url or not mint or not destination:
            return VerificationResult("pending", "Solana verifier requires RPC, token mint, and destination configuration", "Solana", tx_reference)
        if not tx_reference:
            return VerificationResult("pending", "transaction signature is missing", "Solana")
        if not self._is_valid_solana_signature(tx_reference):
            return VerificationResult("rejected", "Solana transaction signature format is invalid", "Solana", tx_reference)

        try:
            payload = self._post_json(
                rpc_url,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        tx_reference,
                        {
                            "commitment": "finalized",
                            "encoding": "jsonParsed",
                            "maxSupportedTransactionVersion": 0,
                        },
                    ],
                },
                {"Content-Type": "application/json"},
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            logger.warning(
                '{"event":"payment_verification_provider_error","network":"Solana","error":"%s"}',
                str(exc)[:160],
            )
            return VerificationResult("unknown", "Solana provider could not be reached", "Solana", tx_reference)

        result = payload.get("result")
        provider_error = payload.get("error")
        if isinstance(provider_error, dict):
            if provider_error.get("code") == -32602:
                return VerificationResult("rejected", "Solana provider rejected the transaction signature", "Solana", tx_reference)
            return VerificationResult("unknown", "Solana provider returned an error", "Solana", tx_reference)
        if not isinstance(result, dict):
            return VerificationResult("pending", "Solana transaction is not finalized or was not found", "Solana", tx_reference)
        meta = result.get("meta") or {}
        if meta.get("err") is not None:
            return VerificationResult("rejected", "Solana transaction failed", "Solana", tx_reference)
        timestamp_result = self._transaction_time_result(
            intent,
            result.get("blockTime"),
            "Solana",
            tx_reference,
        )
        if timestamp_result is not None:
            return timestamp_result

        expected = self._expected_decimal(intent)
        decimals = int(getattr(self._config, "solana_token_decimals", 6) or 6)
        expected_base_units = expected * (Decimal(10) ** decimals)
        if not self._has_matching_solana_transfer(
            result,
            destination_owner=destination,
            mint=mint,
            expected_base_units=expected_base_units,
        ):
            return VerificationResult(
                "rejected",
                "Solana transaction did not contain a USDC transfer to ATLAX",
                "Solana",
                tx_reference,
            )
        pre_balances = {
            item.get("accountIndex"): Decimal(str((item.get("uiTokenAmount") or {}).get("amount", "0")))
            for item in (meta.get("preTokenBalances") or [])
            if isinstance(item, dict)
        }
        for item in meta.get("postTokenBalances") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("mint", "")) != mint or str(item.get("owner", "")) != destination:
                continue
            observed_base_units = Decimal(str((item.get("uiTokenAmount") or {}).get("amount", "0"))) - pre_balances.get(item.get("accountIndex"), Decimal("0"))
            if observed_base_units == expected_base_units:
                return VerificationResult("verified", "Solana token transfer matched", "Solana", tx_reference, expected)
            return VerificationResult("rejected", "Solana token amount did not match the plan", "Solana", tx_reference, observed_base_units / (Decimal(10) ** decimals))
        return VerificationResult("rejected", "Solana token transfer to ATLAX was not found", "Solana", tx_reference)

    @classmethod
    def _has_matching_solana_transfer(
        cls,
        transaction: dict[str, Any],
        *,
        destination_owner: str,
        mint: str,
        expected_base_units: Decimal,
    ) -> bool:
        """Require a parsed SPL transfer instruction to ATLAX's token account."""
        message = ((transaction.get("transaction") or {}).get("message") or {})
        account_keys = message.get("accountKeys") or []
        key_by_index: dict[int, str] = {}
        for index, item in enumerate(account_keys):
            if isinstance(item, dict):
                key = item.get("pubkey")
            else:
                key = item
            if key:
                key_by_index[index] = str(key)

        destination_accounts = set()
        meta = transaction.get("meta") or {}
        for balance in meta.get("postTokenBalances") or []:
            if not isinstance(balance, dict):
                continue
            if str(balance.get("mint", "")) != mint:
                continue
            if str(balance.get("owner", "")) != destination_owner:
                continue
            account_key = key_by_index.get(balance.get("accountIndex"))
            if account_key:
                destination_accounts.add(account_key)
        if not destination_accounts:
            return False

        instructions = list(message.get("instructions") or [])
        instructions.extend(
            instruction
            for group in (meta.get("innerInstructions") or [])
            if isinstance(group, dict)
            for instruction in (group.get("instructions") or [])
        )
        for instruction in instructions:
            if not isinstance(instruction, dict):
                continue
            if instruction.get("program") not in {"spl-token", "spl-token-2022"}:
                continue
            parsed = instruction.get("parsed") or {}
            if parsed.get("type") not in {"transfer", "transferChecked"}:
                continue
            info = parsed.get("info") or {}
            if str(info.get("destination", "")) not in destination_accounts:
                continue
            if parsed.get("type") == "transferChecked":
                token_amount = (info.get("tokenAmount") or {}).get("amount")
                instruction_mint = str(info.get("mint", ""))
                if instruction_mint and instruction_mint != mint:
                    continue
            else:
                token_amount = info.get("amount")
            try:
                if Decimal(str(token_amount)) == expected_base_units:
                    return True
            except (InvalidOperation, TypeError, ValueError):
                continue
        return False

    @staticmethod
    def _expected_decimal(intent: dict[str, Any]) -> Decimal:
        try:
            return Decimal(str(intent.get("amount_usd", "0")))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")

    def _transaction_time_result(
        self,
        intent: dict[str, Any],
        observed_timestamp: Any,
        network: str,
        tx_reference: str,
    ) -> Optional[VerificationResult]:
        """Bind an on-chain transfer to this payment reference's time window."""
        try:
            observed_epoch = float(observed_timestamp)
        except (TypeError, ValueError):
            return VerificationResult(
                "unknown",
                f"{network} transaction timestamp was unavailable",
                network,
                tx_reference,
            )
        if observed_epoch <= 0:
            return VerificationResult(
                "unknown",
                f"{network} transaction timestamp was invalid",
                network,
                tx_reference,
            )

        raw_created_at = str(intent.get("created_at", "")).strip()
        try:
            created_at = datetime.fromisoformat(raw_created_at.replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            created_epoch = created_at.astimezone(timezone.utc).timestamp()
        except (TypeError, ValueError, OverflowError):
            return VerificationResult(
                "unknown",
                "payment reference timestamp was unavailable",
                network,
                tx_reference,
            )

        skew = max(
            0,
            int(getattr(self._config, "verification_clock_skew_seconds", 300) or 0),
        )
        if observed_epoch < created_epoch - skew:
            return VerificationResult(
                "rejected",
                f"{network} transaction predates this payment reference",
                network,
                tx_reference,
            )
        if observed_epoch > datetime.now(timezone.utc).timestamp() + skew:
            return VerificationResult(
                "rejected",
                f"{network} transaction timestamp is in the future",
                network,
                tx_reference,
            )
        return None

    @staticmethod
    def _same_value(left: Any, right: Any) -> bool:
        return str(left or "").strip().lower() == str(right or "").strip().lower()

    @staticmethod
    def _is_valid_solana_signature(value: str) -> bool:
        """Validate the base58 encoding of a 64-byte Solana signature."""
        raw = str(value or "").strip()
        if not raw or any(character not in _SOLANA_BASE58_ALPHABET for character in raw):
            return False
        decoded = 0
        for character in raw:
            decoded = decoded * 58 + _SOLANA_BASE58_ALPHABET.index(character)
        leading_zeroes = len(raw) - len(raw.lstrip("1"))
        decoded_length = leading_zeroes if decoded == 0 else (decoded.bit_length() + 7) // 8 + leading_zeroes
        return decoded_length == 64

    def _default_get_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        request = Request(url, headers=headers, method="GET")
        timeout = int(getattr(self._config, "verification_timeout_seconds", 15) or 15)
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _default_post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        timeout = int(getattr(self._config, "verification_timeout_seconds", 15) or 15)
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
