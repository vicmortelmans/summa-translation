#!/usr/bin/env python3
"""Compare a run's preliminary and harmonised translation layers.

Reads both glossary application inventories and both consistency reports,
counts how many occurrences still do not use the preferred rendering (using the
reviewed form mapping), and writes a before/after comparison.

Usage::

    ./env/bin/python terminology_verify.py run-glossary
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

from terminology_common import (
    CONSISTENCY_DIR_NAME,
    TERMINOLOGY_DIR_NAME,
    TRANSLATION_DIR_NAME,
    load_review_rows,
    resolve_review_csv,
    rows_for_lemma,
    term_present,
    write_csv,
)
from terminology_plan import group_forms

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

VERIFICATION_DIR_NAME = "50_verification"
INVENTORY_NAME = "glossary-application-inventory.csv"
CHANGES_DIR_NAME = "40_changes"


def load_inventory(path: Path) -> list[dict]:
    """Read an inventory CSV."""
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_translations(directory: Path) -> dict[str, str]:
    """Load ``id -> translation`` from a layer."""
    merged: dict[str, str] = {}
    for path in sorted(directory.glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        records = payload.get("translations") if isinstance(payload, dict) else payload
        for record in records:
            merged[str(record["id"])] = record["translation"]
    return merged


def preferred_for(lemma: str, source: str, rows) -> str:
    """Return the reviewed rendering that applies to a surface form."""
    groups, _uncovered = group_forms([source], rows_for_lemma(rows, lemma))
    return groups[0].preferred if groups else ""


def inventory_stats(
    inventory: list[dict], translations: dict[str, str], rows
) -> dict[str, dict[str, int]]:
    """Count total and non-conforming glossary applications per lemma."""
    stats: dict[str, dict[str, int]] = {}
    for row in inventory:
        lemma = row["lemma"]
        entry = stats.setdefault(lemma, {"total": 0, "nonconforming": 0})
        entry["total"] += 1
        preferred = preferred_for(lemma, row["source"], rows)
        translation = translations.get(row["sentence_id"], "")
        if not preferred or not term_present(preferred, translation):
            entry["nonconforming"] += 1
    return stats


def load_changes(changes_dir: Path) -> dict[str, dict]:
    """Load the per-lemma apply summaries."""
    result: dict[str, dict] = {}
    for path in sorted((changes_dir / "summary").glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            summary = json.load(handle)
        result[summary["lemma"]] = summary
    return result


def load_candidates(path: Path) -> dict[str, dict[str, str]]:
    """Load the corrected candidate lemmas, if the analysis produced any."""
    result: dict[str, dict[str, str]] = {}
    if not path.is_file():
        return result
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            result[row["lemma"]] = row
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare preliminary and harmonised terminology consistency"
    )
    parser.add_argument("run_dir", nargs="?", default="run-glossary")
    parser.add_argument("--terminology-dir",
                        help=f"terminology root (default: <run>/{TERMINOLOGY_DIR_NAME})")
    parser.add_argument("--review-csv", help="reviewed disambiguation CSV")
    parser.add_argument("--verification-dir",
                        help=f"verification output directory (default: <term>/{VERIFICATION_DIR_NAME})")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    term_dir = (Path(args.terminology_dir) if args.terminology_dir
                else run_dir / TERMINOLOGY_DIR_NAME)
    verification_dir = (Path(args.verification_dir) if args.verification_dir
                        else term_dir / VERIFICATION_DIR_NAME)
    verification_dir.mkdir(parents=True, exist_ok=True)

    try:
        rows = load_review_rows(resolve_review_csv(run_dir, args.review_csv))
        before_inventory = load_inventory(run_dir / INVENTORY_NAME)
        after_inventory = load_inventory(verification_dir / INVENTORY_NAME)
        if not before_inventory:
            raise ValueError(f"inventory not found: {run_dir / INVENTORY_NAME}")
        if not after_inventory:
            raise ValueError(f"corrected inventory not found: {verification_dir / INVENTORY_NAME}")
        before_translations = load_translations(run_dir / TRANSLATION_DIR_NAME)
        after_translations = load_translations(term_dir / TRANSLATION_DIR_NAME)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    before = inventory_stats(before_inventory, before_translations, rows)
    after = inventory_stats(after_inventory, after_translations, rows)
    changes = load_changes(term_dir / CHANGES_DIR_NAME)
    candidates = load_candidates(
        verification_dir / CONSISTENCY_DIR_NAME / "candidate_lemmas.csv"
    )

    lemmas = list(dict.fromkeys([row.lemma for row in rows if row.translation]))
    output_rows: list[list[str]] = []
    for lemma in lemmas:
        before_stats = before.get(lemma, {"total": 0, "nonconforming": 0})
        after_stats = after.get(lemma, {"total": 0, "nonconforming": 0})
        summary = changes.get(lemma, {})
        candidate = candidates.get(lemma)
        output_rows.append([
            lemma,
            str(before_stats["total"]),
            str(before_stats["nonconforming"]),
            str(after_stats["total"]),
            str(after_stats["nonconforming"]),
            str(summary.get("planned", "")),
            str(summary.get("applied", "")),
            str(summary.get("kept", "")),
            str(summary.get("unverified", "")),
            "yes" if candidate else "no",
            candidate.get("switch_occurrences", "") if candidate else "",
        ])

    write_csv(
        verification_dir / "comparison.csv",
        ["lemma", "before_total", "before_nonconforming", "after_total",
         "after_nonconforming", "planned", "applied", "kept", "unverified",
         "still_candidate", "switch_occurrences"],
        output_rows,
    )

    lines = [
        "# Terminology verification",
        "",
        "| lemma | before non-conforming | after non-conforming | applied | kept | still candidate |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in output_rows:
        lemma, _bt, before_bad, _at, after_bad, _p, applied, kept, _u, candidate, _s = row
        lines.append(
            f"| **{lemma}** | {before_bad} | {after_bad} | {applied} | {kept} | {candidate} |"
        )
    lines.extend([
        "",
        "Counts are per glossary application in the inventory (one row per sentence "
        "and lemma), using the reviewed form mapping and the same conservative "
        "inflection rules as the plan.",
        "",
    ])
    (verification_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    logger.info("Comparison written to: %s", verification_dir / "comparison.csv")
    for row in output_rows:
        logger.info("  %-14s %4s -> %4s non-conforming, still candidate: %s",
                    row[0], row[2], row[4], row[9])


if __name__ == "__main__":
    main()

