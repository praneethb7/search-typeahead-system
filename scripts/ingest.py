"""Load data/queries.csv into the SQLite primary store.

Run from the repo root:
    python scripts/ingest.py
    python scripts/ingest.py --csv data/queries.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time

# Make `import backend...` work when run as a plain script (`python scripts/ingest.py`).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.store.db import store  # noqa: E402


def rows_from_csv(path: str):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            query = (row.get("query") or "").strip().lower()
            count_raw = (row.get("count") or "0").strip()
            if not query:
                continue
            try:
                count = int(float(count_raw))
            except ValueError:
                count = 0
            yield query, count


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest the dataset CSV into SQLite.")
    parser.add_argument("--csv", default="data/queries.csv", help="input CSV path")
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        raise SystemExit(
            f"{args.csv} not found. Generate it first:\n  python scripts/generate_dataset.py"
        )

    start = time.time()
    written = store.bulk_insert(rows_from_csv(args.csv))
    elapsed = time.time() - start
    print(f"Ingested {written:,} queries into {store.db_path} in {elapsed:.1f}s")
    print(f"Total rows in store: {store.row_count():,}")


if __name__ == "__main__":
    main()
