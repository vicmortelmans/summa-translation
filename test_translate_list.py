#!/usr/bin/env python3
"""Process the first MAX_ROWS Latin sentences from to_be_translated.tsv through the
similarity search and translation pipeline.

This script:
1. Reads the first MAX_ROWS lines from to_be_translated.tsv.
2. For each Latin sentence, retrieves 5 similar Latin-Dutch sentence pairs using
   find_similar() from vector/search.py.
3. Builds the list of dictionaries required by translate_sentences() in
   translate_list.py.
4. Calls translate_sentences(..., online=True).
5. Writes the translations to a date-time-stamped text file, including the Latin
   source sentence.
6. Writes the finalized input list as JSON to a matching date-time-stamped file.
"""

import csv
import json
import argparse
import sys
from datetime import datetime
from pathlib import Path

from translate_list import translate_sentences
from vector.search import find_similar

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


BASE_DIR = Path(__file__).resolve().parent
TSV_PATH = BASE_DIR / "to_be_translated.tsv"
MAX_ROWS = 500


def read_rows_range(path: Path, start_id: int, end_id: int) -> list[dict[str, str]]:
    """Read rows from `start_id` to `end_id` (inclusive) from a TSV file.

    The TSV may contain an explicit id in the third column; when present that
    id is used. Otherwise the sequential data-row index (starting at 1) is
    used to match the requested range.
    """
    if start_id is None or end_id is None:
        raise ValueError("start_id and end_id must be provided")
    if start_id <= 0 or end_id < start_id:
        raise ValueError("Invalid start_id/end_id range")

    rows: list[dict[str, str]] = []

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")

        # Skip header
        next(reader, None)

        data_index = 0
        for raw in reader:
            # data_index counts data rows (excluding header)
            data_index += 1
            if not raw or not raw[0].strip():
                continue

            latin = raw[0].strip()
            row_id = None
            if len(raw) > 2 and raw[2].strip():
                try:
                    row_id = int(raw[2].strip())
                except Exception:
                    # fallback to data index if third column isn't an int
                    row_id = data_index
            else:
                row_id = data_index

            if row_id < start_id:
                continue
            if row_id > end_id:
                break

            rows.append({"id": str(row_id), "source": latin})

    return rows


def build_translation_input(rows: list[dict[str, str]]) -> list[dict]:
    """Compose the list of dictionaries required by translate_sentences()."""
    payload: list[dict] = []

    for index, row in enumerate(rows, start=1):
        latin = row["source"]
        similar = find_similar(latin, n=5)

        references = [
            {
                "source": item["latin"],
                "translation": item["dutch"],
            }
            for item in similar
        ]

        payload.append(
            {
                "id": str(row["id"]),
                "source": latin,
                "references": references,
            }
        )

    return payload


def write_text_output(timestamp: str, items: list[dict], translations: list[dict]) -> Path:
    """Write a text file containing each source sentence and its translation."""
    output_path = BASE_DIR / f"translations_{timestamp}.txt"

    translation_map = {entry["id"]: entry["translation"] for entry in translations}

    lines: list[str] = []
    for item in items:
        item_id = str(item["id"])
        source = item["source"]
        translation = translation_map.get(item_id, "")
        lines.append(f"ID: {item_id}")
        lines.append(f"Latin: {source}")
        lines.append(f"Dutch: {translation}")
        lines.append("-" * 80)

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch translate range of TSV sentences")
    parser.add_argument("--start-id", type=int, required=True, help="Start id (inclusive)")
    parser.add_argument("--end-id", type=int, required=True, help="End id (inclusive)")
    parser.add_argument("--batch-size", type=int, default=MAX_ROWS, help=f"Batch size (default: {MAX_ROWS})")
    args = parser.parse_args()

    try:
        rows = read_rows_range(TSV_PATH, args.start_id, args.end_id)
    except Exception as exc:
        print(f"Error reading TSV: {exc}", file=sys.stderr)
        raise

    if not rows:
        print("No rows found for the requested range.")
        return

    # Split into batches of `batch_size` and process sequentially.
    total = len(rows)
    batches = [rows[i : i + args.batch_size] for i in range(0, total, args.batch_size)]

    for batch_index, batch_rows in enumerate(batches, start=1):
        payload = build_translation_input(batch_rows)
        translations = translate_sentences(payload, online=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = f"_{args.start_id}-{args.end_id}_batch{batch_index}"

        json_path = BASE_DIR / f"translation_input_{timestamp}{suffix}.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        text_path = BASE_DIR / f"translations_{timestamp}{suffix}.txt"
        write_text_output(f"{timestamp}{suffix}", batch_rows, translations)

        print(f"Batch {batch_index}/{len(batches)}: processed {len(batch_rows)} sentences")
        print(f"JSON input written to: {json_path}")
        print(f"Translations written to: {text_path}")


if __name__ == "__main__":
    main()
