"""Run one explicit batch of the approved TradingView shadow consumer."""

from __future__ import annotations

import argparse
import json

from core.api.webhook_consumer import WebhookShadowConsumer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of queued events to claim in this batch",
    )
    args = parser.parse_args()
    summary = WebhookShadowConsumer().consume_once(limit=args.limit)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
