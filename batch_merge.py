#!/usr/bin/env python3
"""Merge the translation batches of a job into one file.

The script reads every JSON translation file in ``<job name>/30_translation``
and merges their ``translations`` lists into a single JSON object.  It also
accumulates the token usage of all batches (per category) and writes both
results to ``<job name>/40-merge``:

- ``translations.json``: all translation records in batch order
- ``tokens.json``: the summed token counts per category
- ``batch_merge_command.txt``: the command line used to run this script

The job name is the directory created by ``batch_input.py`` (``run-<tag>``).
It can be passed directly or derived from a tag with ``--tag``.

Usage:
    ./env/bin/python batch_merge.py --tag test3
    ./env/bin/python batch_merge.py run-test3
"""

import argparse
import json
import logging
import re
import shlex
import sys
from pathlib import Path
from typing import Any

# ============================================================================
# Logging
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================================
# Configuration
# ============================================================================

BASE_DIR = Path(__file__).resolve().parent
TRANSLATION_DIR_NAME = "30_translation"
TOKEN_DIR_NAME = "tokens"
MERGE_DIR_NAME = "40-merge"
SCRIPT_STEM = Path(__file__).stem

TRANSLATIONS_OUTPUT_NAME = "translations.json"
TOKENS_OUTPUT_NAME = "tokens.json"


# ============================================================================
# Helpers
# ============================================================================


def natural_sort_key(path: Path) -> list[Any]:
    """Return a sort key that orders ``batch2`` before ``batch10``."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    ]


def load_translation_records(path: Path) -> list[dict]:
    """Read one translation file and return its list of translation records."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Translation file '{path}' is not valid JSON: {exc}") from exc

    if isinstance(data, dict):
        records = data.get("translations")
        if not isinstance(records, list):
            raise ValueError(
                f"Translation file '{path}' must contain a 'translations' list."
            )
    elif isinstance(data, list):
        # Be tolerant of files that contain the bare list of records.
        records = data
    else:
        raise ValueError(
            f"Translation file '{path}' must contain a JSON object or array."
        )

    for index, record in enumerate(records):
        if not isinstance(record, dict) or "id" not in record:
            raise ValueError(
                f"Translation record {index} in '{path}' does not contain an 'id' field."
            )

    return records


def load_token_counts(path: Path) -> dict[str, int]:
    """Read one token file and return its token counts per category."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Token file '{path}' is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Token file '{path}' must contain a JSON object.")

    counts: dict[str, int] = {}
    for category, value in data.items():
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(
                f"Token category '{category}' in '{path}' must be an integer."
            )
        counts[category] = value
    return counts


def resolve_job_dir(job: str | None, tag: str | None) -> Path:
    """Resolve the job directory from a positional job name or a tag."""
    if tag:
        return BASE_DIR / f"run-{tag}"

    if not job:
        raise ValueError("Provide a job name or --tag.")

    job_path = Path(job)
    if not job_path.is_absolute():
        job_path = BASE_DIR / job_path

    # Allow a bare tag as a convenience, e.g. "test3" -> "run-test3".
    if not job_path.exists() and not job.startswith("run-"):
        alternative = BASE_DIR / f"run-{job}"
        if alternative.exists():
            job_path = alternative

    return job_path


# ============================================================================
# Merge
# ============================================================================


def merge_translations(translation_dir: Path) -> tuple[list[dict], int]:
    """Merge all translation files and return the records and file count."""
    translation_files = sorted(translation_dir.glob("*.json"), key=natural_sort_key)
    if not translation_files:
        raise ValueError(f"No JSON translation files found in '{translation_dir}'.")

    merged: list[dict] = []
    seen_ids: dict[str, str] = {}
    duplicate_ids: list[str] = []

    for path in translation_files:
        records = load_translation_records(path)
        for record in records:
            record_id = str(record["id"])
            if record_id in seen_ids:
                duplicate_ids.append(record_id)
            else:
                seen_ids[record_id] = path.name
            merged.append(record)

    if duplicate_ids:
        preview = ", ".join(duplicate_ids[:10])
        suffix = "..." if len(duplicate_ids) > 10 else ""
        raise ValueError(
            "Duplicate translation ids found across batches: "
            f"{preview}{suffix}. Refusing to merge."
        )

    return merged, len(translation_files)


def accumulate_tokens(token_dir: Path) -> tuple[dict[str, int], int]:
    """Sum all token counts per category and return the totals and file count."""
    token_files = sorted(token_dir.glob("*.json"), key=natural_sort_key)
    totals: dict[str, int] = {}

    for path in token_files:
        for category, value in load_token_counts(path).items():
            totals[category] = totals.get(category, 0) + value

    return totals, len(token_files)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge a job's translation batches and token usage."
    )
    parser.add_argument(
        "job",
        nargs="?",
        help="Job name or path (e.g. run-test3).",
    )
    parser.add_argument(
        "--tag",
        type=str,
        help="Tag of the job; resolves to run-<tag> (e.g. --tag test3).",
    )
    args = parser.parse_args()

    if args.job and args.tag:
        parser.error("Provide either a job name or --tag, not both.")

    try:
        job_dir = resolve_job_dir(args.job, args.tag)
    except ValueError as exc:
        parser.error(str(exc))

    if not job_dir.is_dir():
        print(f"Job directory '{job_dir}' does not exist.", file=sys.stderr)
        sys.exit(1)

    translation_dir = job_dir / TRANSLATION_DIR_NAME
    if not translation_dir.is_dir():
        print(
            f"Translation directory '{translation_dir}' does not exist.",
            file=sys.stderr,
        )
        sys.exit(1)

    merge_dir = job_dir / MERGE_DIR_NAME
    merge_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {merge_dir}")

    # Write the command line used to start the script.
    cmd_line = " ".join([shlex.quote(arg) for arg in sys.argv])
    command_file = merge_dir / f"{SCRIPT_STEM}_command.txt"
    with command_file.open("a", encoding="utf-8") as handle:
        handle.write(cmd_line + "\n")

    try:
        translations, translation_file_count = merge_translations(translation_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    translations_path = merge_dir / TRANSLATIONS_OUTPUT_NAME
    translations_path.write_text(
        json.dumps({"translations": translations}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    token_dir = translation_dir / TOKEN_DIR_NAME
    if token_dir.is_dir():
        try:
            token_totals, token_file_count = accumulate_tokens(token_dir)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
    else:
        logger.warning("Token directory '%s' does not exist; writing empty totals.", token_dir)
        token_totals, token_file_count = {}, 0

    tokens_path = merge_dir / TOKENS_OUTPUT_NAME
    tokens_path.write_text(
        json.dumps(token_totals, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Merged {len(translations)} translations from {translation_file_count} files")
    print(f"JSON output written to: {translations_path}")
    print(f"Accumulated tokens from {token_file_count} files: {token_totals}")
    print(f"Token output written to: {tokens_path}")


if __name__ == "__main__":
    main()
