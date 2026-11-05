#!/usr/bin/env python3
import csv
import json
import argparse
import sys
import shlex
from pathlib import Path

import logging

# ============================================================================
# Logging
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
MAX_ROWS = 500


def read_rows_range(path: Path, start_id: int, end_id: int) -> list[dict[str, str, str, str]]:
    """Read rows from `start_id` to `end_id` (inclusive) from a TSV file.

    Output fields:
    - id
    - source
    - lemma reference
    - sequence in lemma
    """
    if start_id is None or end_id is None:
        raise ValueError("start_id and end_id must be provided")
    if start_id <= 0 or end_id < start_id:
        raise ValueError("Invalid start_id/end_id range")

    rows: list[dict[str, str, str, str]] = []

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")

        # Skip header
        next(reader, None)

        data_index = 0
        for raw in reader:
            # data_index counts data rows (excluding header)
            data_index += 1
            if data_index < start_id:
                continue
            if data_index > end_id:
                break

            if not raw or not raw[0].strip():
                continue

            latin = raw[0].strip()
            reference = raw[1].strip() if len(raw) > 1 else ""
            sequence = raw[2].strip() if len(raw) > 2 else ""

            rows.append({"id": str(data_index), "source": latin, "reference": reference, "sequence": sequence})

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch translate range of TSV sentences")
    parser.add_argument("filepath", type=str, help="Path to input TSV file")
    parser.add_argument("--start-id", type=int, required=True, help="Start id (inclusive)")
    parser.add_argument("--end-id", type=int, required=True, help="End id (inclusive)")
    parser.add_argument("--batch-size", type=int, default=MAX_ROWS, help=f"Batch size (default: {MAX_ROWS})")
    parser.add_argument("--tag", type=str, help=f"tag to append to output files (default: none)")
    args = parser.parse_args()
    try:
        rows = read_rows_range(Path(args.filepath), args.start_id, args.end_id)
    except Exception as exc:
        print(f"Error reading TSV: {exc}", file=sys.stderr)
        raise

    if not rows:
        print("No rows found for the requested range.")
        return

    # All outputs of this run go into a single run-specific directory.
    run_dir = BASE_DIR / f"run-{args.tag}" / "10_input" 
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {run_dir}")
    # Write the command line used to start the script
    cmd_line = ' '.join([shlex.quote(arg) for arg in sys.argv])
    (run_dir / "batch_input_command.txt").write_text(cmd_line, encoding="utf-8")

    # Split into batches of `batch_size` and process sequentially.
    total = len(rows)
    batches = [rows[i : i + args.batch_size] for i in range(0, total, args.batch_size)]

    for batch_index, batch_rows in enumerate(batches, start=1):
        stem = f"batch{batch_index}"
        if args.tag:
            stem += f"_{args.tag}"

        json_path = run_dir / f"{stem}.json"
        json_path.write_text(json.dumps(batch_rows, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"Batch {batch_index}/{len(batches)}: written {len(batch_rows)} sentences")
        print(f"JSON input written to: {json_path}")


if __name__ == "__main__":
    main()

