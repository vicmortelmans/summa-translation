#!/usr/bin/env python3
"""
Extract terminology pairs from Latin and Dutch texts using AI.

This script reads Latin-Dutch text pairs from the database, generates
prompts using a template, processes them via AI, and stores the results
in the TerminologySet table.

Usage:
    python extract_terms.py [num_rows]

Args:
    num_rows: Maximum number of rows to process (default: 100)
"""

import sqlite3
import json
import sys
import argparse
from pathlib import Path
from typing import List, Tuple, Optional
from datetime import datetime

from ai import get_responses


BATCH_SIZE = 100
DB_FILE = "summa.db"
TEMPLATE_FILE = "extract_terms.txt"


def load_template(template_file: str) -> str:
    """Load prompt template from file."""
    with open(template_file, "r", encoding="utf-8") as f:
        return f.read()


def fetch_unprocessed_rows(
    conn: sqlite3.Connection, limit: int
) -> List[Tuple[str, str, str]]:
    """Fetch unprocessed LatinByLemma rows with their Dutch counterparts.

    Returns:
        List of (id, latin_text, dutch_text) tuples
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT l.Id, l.Text, COALESCE(d.Text, '')
        FROM LatinByLemma l
        LEFT JOIN DutchByLemma d ON l.Id = d.Id
        WHERE l.ExtractTermsStatus = 0
        LIMIT ?
        """,
        (limit,),
    )
    return cursor.fetchall()


def compose_prompts(rows: List[Tuple[str, str, str]], template: str) -> List[str]:
    """Compose prompts from template and text pairs."""
    prompts = []
    for _, latin_text, dutch_text in rows:
        prompt = template.replace("{latin}", latin_text).replace("{dutch}", dutch_text)
        prompts.append(prompt)
    return prompts


def parse_json_response(response: str) -> Optional[dict]:
    """Parse JSON from AI response, handling potential formatting issues."""
    try:
        # Try direct JSON parsing first
        return json.loads(response)
    except json.JSONDecodeError:
        try:
            # Try extracting JSON from response
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass
    return None


def insert_terminology(
    cursor: sqlite3.Cursor, parsed_data: dict, lemma_id: str
) -> Tuple[int, int]:
    """Insert parsed terminology into TerminologySet.

    Returns:
        (inserted_count, error_count)
    """
    inserted = 0
    errors = 0

    if not parsed_data or "entries" not in parsed_data:
        return 0, 1

    for entry in parsed_data["entries"]:
        try:
            latin_word = entry.get("latin", entry.get("latin_word", "")).strip()
            dutch_word = entry.get("dutch", entry.get("dutch_translation", "")).strip()
            latin_context = entry.get("latin_context", "").strip()
            dutch_context = entry.get("dutch_context", "").strip()
            type_field = entry.get("type", "").strip()

            if not latin_word or not dutch_word:
                errors += 1
                continue

            cursor.execute(
                """
                INSERT OR IGNORE INTO TerminologySet
                (LatinWord, DutchWord, LatinContext, DutchContext, Type)
                VALUES (?, ?, ?, ?, ?)
                """,
                (latin_word, dutch_word, latin_context, dutch_context, type_field),
            )
            inserted += 1
        except (KeyError, TypeError):
            errors += 1

    return inserted, errors


def process_batch(
    conn: sqlite3.Connection,
    rows: List[Tuple[str, str, str]],
    prompts: List[str],
    responses: List[str],
    verbosity: int = 0,
) -> Tuple[int, int, int, int]:
    """Process batch: parse responses and update database.

    Args:
        verbosity: 0=silent, 1=progress, 2+=prompts and responses

    Returns:
        (rows_processed, terminology_inserted, terminology_errors, update_errors)
    """
    cursor = conn.cursor()
    rows_processed = 0
    terminology_inserted = 0
    terminology_errors = 0
    update_errors = 0

    for idx, ((lemma_id, _, _), prompt, response) in enumerate(
        zip(rows, prompts, responses)
    ):
        try:
            if verbosity >= 2:
                print(f"\n--- Processing {idx + 1}/{len(rows)} ---")
                print(f"Lemma ID: {lemma_id}")
                print(f"Prompt:\n{prompt}")
                print(f"\nResponse:\n{response}")

            parsed = parse_json_response(response)

            if parsed:
                inserted, errors = insert_terminology(cursor, parsed, lemma_id)
                terminology_inserted += inserted
                terminology_errors += errors

            # Mark as processed
            cursor.execute(
                "UPDATE LatinByLemma SET ExtractTermsStatus = 1 WHERE Id = ?",
                (lemma_id,),
            )
            rows_processed += 1

            if verbosity >= 1:
                print(f"Processed: {lemma_id}")

        except Exception as e:
            update_errors += 1
            if verbosity >= 1:
                print(f"Error processing {lemma_id}: {e}")

    conn.commit()
    return rows_processed, terminology_inserted, terminology_errors, update_errors


def main():
    parser = argparse.ArgumentParser(
        description="Extract terminology from Latin-Dutch text pairs using AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python extract_terms.py           # Process 100 rows (default batch size)
  python extract_terms.py 500       # Process 500 rows
  python extract_terms.py -v 50     # Process 50 rows with verbose output
        """,
    )

    parser.add_argument(
        "num_rows",
        nargs="?",
        type=int,
        default=BATCH_SIZE,
        help=f"Number of rows to process (default: {BATCH_SIZE})",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Print detailed progress (-v) or include prompts and responses (-vv)",
    )

    parser.add_argument(
        "-d",
        "--database",
        default=DB_FILE,
        help=f"Path to database file (default: {DB_FILE})",
    )

    parser.add_argument(
        "--template",
        default=TEMPLATE_FILE,
        help=f"Path to prompt template file (default: {TEMPLATE_FILE})",
    )

    parser.add_argument(
        "--ai",
        default="online",
        choices=["online", "openai", "local", "vllm"],
        help="AI backend to use (default: online)",
    )

    args = parser.parse_args()

    # Validate inputs
    if args.num_rows <= 0:
        print("Error: num_rows must be positive", file=sys.stderr)
        sys.exit(1)

    db_path = Path(args.database)
    if not db_path.exists():
        print(f"Error: Database not found: {args.database}", file=sys.stderr)
        sys.exit(1)

    template_path = Path(args.template)
    if not template_path.exists():
        print(f"Error: Template not found: {args.template}", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"Database: {db_path.absolute()}")
        print(f"Template: {template_path.absolute()}")
        print(f"AI backend: {args.ai}")
        print(f"Max rows to process: {args.num_rows}")
        print()

    # Load template
    try:
        template = load_template(str(template_path))
    except Exception as e:
        print(f"Error loading template: {e}", file=sys.stderr)
        sys.exit(1)

    # Connect to database
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)

    start_time = datetime.now()
    total_rows_processed = 0
    total_terminology_inserted = 0
    total_terminology_errors = 0
    total_update_errors = 0

    try:
        remaining = args.num_rows

        while remaining > 0:
            batch_limit = min(remaining, BATCH_SIZE)

            # Fetch unprocessed rows
            rows = fetch_unprocessed_rows(conn, batch_limit)
            if not rows:
                if args.verbose:
                    print("No more unprocessed rows found")
                break

            if args.verbose:
                print(f"\nBatch: {len(rows)} rows fetched")

            # Compose prompts
            prompts = compose_prompts(rows, template)

            # Get responses from AI
            if args.verbose:
                print(f"Processing {len(prompts)} prompts via {args.ai}...")

            try:
                responses = get_responses(prompts, ai=args.ai)
            except Exception as e:
                print(f"Error calling AI service: {e}", file=sys.stderr)
                sys.exit(1)

            # Process batch and update database
            processed, inserted, errors, update_errors = process_batch(
                conn, rows, prompts, responses, args.verbose
            )

            total_rows_processed += processed
            total_terminology_inserted += inserted
            total_terminology_errors += errors
            total_update_errors += update_errors

            remaining -= len(rows)

            if args.verbose:
                print(
                    f"Batch complete: {processed} rows, "
                    f"{inserted} terminology entries, "
                    f"{errors} errors"
                )

    finally:
        conn.close()

    elapsed = datetime.now() - start_time

    # Print summary
    print("\n" + "=" * 60)
    print("EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"Rows processed:        {total_rows_processed}")
    print(f"Terminology inserted:  {total_terminology_inserted}")
    print(f"Terminology errors:    {total_terminology_errors}")
    print(f"Update errors:         {total_update_errors}")
    print(f"Time elapsed:          {elapsed}")
    print("=" * 60)


if __name__ == "__main__":
    main()
