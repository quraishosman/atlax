"""ATLAX payment helpers with lazy exports for lightweight storage imports."""

__all__ = ["PaymentService", "PaymentStore"]


def __getattr__(name: str):
    if name == "PaymentService":
        from .payment_service import PaymentService

        return PaymentService
    if name == "PaymentStore":
        from .payment_store import PaymentStore

        return PaymentStore
    raise AttributeError(name)
