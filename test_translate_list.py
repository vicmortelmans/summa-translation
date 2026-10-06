#!/usr/bin/env python3
"""Process the first MAX_ROWS Latin sentences from to_be_translated.tsv through the
similarity search and translation pipeline.

This script:
1. Reads the first MAX_ROWS lines from to_be_translated.tsv.
2. For each Latin sentence, retrieves 5 similar Latin-Dutch sentence pairs using
   find_similar() from vector/search.py.
3. Builds the list of dictionaries required by translate_sentences() in
   translate_list.py.
4. Calls translate_sentences(...).
5. Writes the translations to a date-time-stamped text file, including the Latin
   source sentence.
6. Writes the finalized input list as JSON to a matching date-time-stamped file.
"""

import csv
import json
import argparse
import os
import sys
import shlex
from datetime import datetime
from pathlib import Path
from openai import OpenAI

from translate_list import translate_sentences
from vector.search import find_similar

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


# ============================================================================
# Configuration
# ============================================================================

OPENAI_API_KEY_FILE = ".openai_api_key"  # fallback if not provided on commmand line
OPENAI_MODEL = "gpt-5.6-luna"  # fallback if not provided on command line
BASE_DIR = Path(__file__).resolve().parent
MAX_ROWS = 500


def _load_openai_api_key() -> str:
    env_key = os.environ.get("OPENAI_API_KEY")
    if env_key:
        return env_key.strip()

    key_path = Path(__file__).resolve().parent / OPENAI_API_KEY_FILE
    if key_path.exists():
        key = key_path.read_text(encoding="utf-8").strip()
        if key:
            return key

    raise RuntimeError(
        "OpenAI API key not found. Please add it to the file '"
        f"{OPENAI_API_KEY_FILE}' in the repository root or set the OPENAI_API_KEY "
        "environment variable."
    )


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


def build_translation_input(rows: list[dict[str, str, str, str]], reference_number: int = 5) -> list[dict]:
    """Compose the list of dictionaries required by translate_sentences()."""
    payload: list[dict] = []

    for index, row in enumerate(rows, start=1):
        latin = row["source"]
        similar = find_similar(latin, n=reference_number)

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

    output = []

    for item in items:
        item_id = str(item["id"])
        source = item["source"]
        reference = item["reference"]
        sequence = item["sequence"]
        translation = translation_map.get(item_id, "")

        output.append({
            "id": item_id,
            "latin": source,
            "dutch": translation,
            "reference": reference,
            "sequence": sequence,
        })

    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def write_tokens_output(timestamp: str, tokens: dict) -> Path:
    """Write a text file containing the token usage counts."""
    output_path = BASE_DIR / f"tokens_{timestamp}.txt"

    output_path.write_text(
        json.dumps(tokens, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def build_batch_error_command(batch_start: int, batch_end: int) -> str:
    """Return the original invocation with the batch start/end line numbers substituted."""
    command_parts = [sys.executable, *sys.argv]
    for i, part in enumerate(command_parts):
        if part == "--start-id" and i + 1 < len(command_parts):
            command_parts[i + 1] = str(batch_start)
        elif part == "--end-id" and i + 1 < len(command_parts):
            command_parts[i + 1] = str(batch_end)
    return shlex.join(command_parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch translate range of TSV sentences")
    parser.add_argument("filepath", type=str, help="Path to input TSV file")
    parser.add_argument("--start-id", type=int, required=True, help="Start id (inclusive)")
    parser.add_argument("--end-id", type=int, required=True, help="End id (inclusive)")
    parser.add_argument("--batch-size", type=int, default=MAX_ROWS, help=f"Batch size (default: {MAX_ROWS})")
    parser.add_argument("--openai-model", type=str, default=OPENAI_MODEL, help=f"OpenAI model (default: {OPENAI_MODEL})")
    parser.add_argument("--reference-number", type=int, default=5, help=f"Number of reference translations (default: 5)")
    parser.add_argument("--reasoning-effort", type=str, default="none", help=f"Reasoning effort (default: none)")
    parser.add_argument("--api-key", type=str, help=f"OpenAI API key (default: from environment or file)")
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

    # Split into batches of `batch_size` and process sequentially.
    total = len(rows)
    batches = [rows[i : i + args.batch_size] for i in range(0, total, args.batch_size)]

    # Create client
    if not args.api_key:
        api_key = _load_openai_api_key()
    else:
        api_key = args.api_key
    openai_client = OpenAI(
        api_key=api_key,
    )

    all_translations = []
    accumulated_tokens = {
        "input": 0,
        "cached": 0,
        "output": 0,
        "reasoning": 0,
    }

    for batch_index, batch_rows in enumerate(batches, start=1):
        payload = build_translation_input(batch_rows, reference_number=args.reference_number)
        batch_start = int(batch_rows[0]["id"])
        batch_end = int(batch_rows[-1]["id"])

        try:
            raise ValueError("Simulated error for testing retry logic")  # Simulate an error for testing
            tokens, translations = translate_sentences(
                payload,
                openai_model=args.openai_model,
                reasoning_effort=args.reasoning_effort,
                openai_client=openai_client,
            )
        except ValueError:
            try:
                raise ValueError("Simulated error for testing retry logic")  # Simulate an error for testing
                tokens, translations = translate_sentences(
                    payload,
                    openai_model=args.openai_model,
                    reasoning_effort=args.reasoning_effort,
                    openai_client=openai_client,
                )
            except ValueError:
                error_file = BASE_DIR / "test_translate_list_errors.txt"
                with error_file.open("a", encoding="utf-8") as handle:
                    handle.write(f"{build_batch_error_command(batch_start, batch_end)}\n")
                print(
                    f"Retry failed for batch {batch_index}/{len(batches)} "
                    f"({batch_start}-{batch_end}); logged command to {error_file}",
                    file=sys.stderr,
                )
                continue

        for key in accumulated_tokens:
            accumulated_tokens[key] += tokens[key]
        all_translations.extend(translations)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = f"_{args.start_id}-{args.end_id}_batch{batch_index}"
        if args.tag:
            suffix += f"_{args.tag}"

        json_path = BASE_DIR / f"translation_input_{timestamp}{suffix}.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        text_path = BASE_DIR / f"translations_{timestamp}{suffix}.txt"
        write_text_output(f"{timestamp}{suffix}", batch_rows, translations)
        write_tokens_output(f"{timestamp}{suffix}", tokens)

        print(f"Batch {batch_index}/{len(batches)}: processed {len(batch_rows)} sentences")
        print(f"JSON input written to: {json_path}")
        print(f"Translations written to: {text_path}")

    suffix = f"_{args.start_id}-{args.end_id}_all"
    if args.tag:
        suffix += f"_{args.tag}"
    write_text_output(f"{timestamp}{suffix}", rows, all_translations)
    write_tokens_output(f"{timestamp}{suffix}", accumulated_tokens)

if __name__ == "__main__":
    main()
