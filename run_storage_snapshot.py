"""Capture a local ATLAX state snapshot for a migration rehearsal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.storage.frozen_snapshot import create_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--snapshot-dir", required=True)
    args = parser.parse_args()
    manifest = create_snapshot(
        Path(args.data_dir).resolve(),
        Path(args.snapshot_dir).resolve(),
    )
    print(json.dumps({"status": "OK", **manifest}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
