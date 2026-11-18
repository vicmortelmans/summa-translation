#!/usr/bin/env python3
"""Write a per-form inventory for the candidate lemmas of a run.

The disambiguation CSV is lemma-level: it asks for one preferred Dutch
rendering per lemma.  Some lemmas are used in several senses, and the surface
form is a good handle to distinguish them (e.g. ``accidens`` as a noun versus
``accidit`` as a verb).  This script lists every Latin form of every candidate
lemma with its occurrence counts, so a reviewer can add form-specific rows to
``inter-batch-lemmata-disambiguation.csv``.

Output (default ``<run>/glossary-consistency/lemma_form_inventory.csv``)::

    lemma,form,occurrences,sentences,recorded_as_entry_source,sentence_variants,example_sentence_ids

``sentence_variants`` is a proxy: the inventory variant of the sentence the
form occurs in (a sentence can contain several forms, and the inventory cannot
attribute a variant to a single token).  ``recorded_as_entry_source`` says
whether stage 3 recorded this form as the glossary entry's ``source`` (it keeps
only the first form per lemma and sentence).

Usage::

    ./env/bin/python terminology_form_inventory.py run-glossary
"""

from __future__ import annotations

import argparse
import csv
import logging
import shlex
import sys
from collections import Counter, defaultdict
from pathlib import Path

from terminology_common import (
    CONSISTENCY_DIR_NAME,
    FORM_INVENTORY_NAME,
    REVIEW_FILE_NAME,
    REVIEWED_FILE_NAME,
    load_glossary_rows,
    load_review_rows,
    review_lemmas,
    write_csv,
)
from terminology_forms import LatinFormFinder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

INVENTORY_NAME = "glossary-application-inventory.csv"
CANDIDATE_FILE_NAME = "candidate_lemmas.csv"


def resolve_candidates(run_dir: Path, review_csv: str | None) -> Path | None:
    """Return the review CSV to read, or ``None`` to fall back to the candidates."""
    if review_csv:
        path = Path(review_csv)
        if not path.is_file():
            raise ValueError(f"review CSV not found: {path}")
        return path
    consistency = run_dir / CONSISTENCY_DIR_NAME
    for name in (REVIEWED_FILE_NAME, REVIEW_FILE_NAME):
        candidate = consistency / name
        if candidate.is_file():
            return candidate
    return None


def load_candidate_lemmas(run_dir: Path, review_csv: str | None) -> list[str]:
    """Return the lemmas to describe, in review-file order."""
    path = resolve_candidates(run_dir, review_csv)
    if path is not None:
        return review_lemmas(load_review_rows(path))
    candidates = run_dir / CONSISTENCY_DIR_NAME / CANDIDATE_FILE_NAME
    if candidates.is_file():
        with candidates.open("r", encoding="utf-8", newline="") as handle:
            return [row["lemma"] for row in csv.DictReader(handle) if row.get("lemma")]
    raise ValueError(f"no review CSV or {CANDIDATE_FILE_NAME} found in {run_dir}")


def load_sentence_variants(run_dir: Path) -> dict[tuple[str, str], str]:
    """Load ``(sentence_id, lemma) -> variant`` from the inventory, if present."""
    path = run_dir / INVENTORY_NAME
    variants: dict[tuple[str, str], str] = {}
    if not path.is_file():
        logger.warning("Inventory not found (%s); leaving the variants column empty.", path)
        return variants
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            index = int(row["definition_index"])
            alternatives = [part.strip() for part in row["translation"].split(",") if part.strip()]
            variant = alternatives[index - 1] if 1 <= index <= len(alternatives) else "__other__"
            variants[(row["sentence_id"], row["lemma"])] = variant
    return variants



def build_rows(
    run_dir: Path,
    lemmas: list[str],
    finder: LatinFormFinder,
    variants: dict[tuple[str, str], str],
    glossary_dir: Path | None = None,
) -> list[list[str]]:
    """Build the CSV rows for all candidate lemmas."""
    glossary_rows = load_glossary_rows(run_dir, glossary_dir)
    rows: list[list[str]] = []
    for lemma in lemmas:
        display: dict[str, str] = {}
        occurrences: Counter[str] = Counter()
        sentences: dict[str, set[str]] = defaultdict(set)
        recorded: set[str] = set()
        variant_counts: dict[str, Counter[str]] = defaultdict(Counter)
        examples: dict[str, list[str]] = defaultdict(list)
        ambiguous = 0
        for _batch, row in glossary_rows:
            entries = [entry for entry in row.get("glossary", []) if entry.get("lemma") == lemma]
            if not entries:
                continue
            if len(entries) > 1:
                ambiguous += 1
                continue
            entry = entries[0]
            source = str(entry.get("source", ""))
            variant = variants.get((row["id"], lemma))
            for token in finder.occurrences(row.get("source", ""), entry):
                key = token.surface.lower()
                display.setdefault(key, token.surface)
                occurrences[key] += 1
                sentences[key].add(row["id"])
                if source and source.lower() == key:
                    recorded.add(key)
                if variant:
                    variant_counts[key][variant] += 1
                if len(examples[key]) < 5 and row["id"] not in examples[key]:
                    examples[key].append(row["id"])
        if ambiguous:
            logger.warning("%s: %d sentences have multiple glossary entries; skipped.",
                           lemma, ambiguous)
        ordered = sorted(occurrences, key=lambda key: (-occurrences[key], key))
        for key in ordered:
            rows.append([
                lemma,
                display[key],
                str(occurrences[key]),
                str(len(sentences[key])),
                "yes" if key in recorded else "no",
                ";".join(f"{name}:{count}" for name, count in sorted(variant_counts[key].items())),
                ";".join(examples[key]),
            ])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write a per-form inventory for the candidate lemmas of a run"
    )
    parser.add_argument("run_dir", nargs="?", default="run-glossary",
                        help="run directory (default: run-glossary)")
    parser.add_argument("--review-csv", help="reviewed disambiguation CSV")
    parser.add_argument("-o", "--output",
                        help=f"output CSV (default: <run>/{CONSISTENCY_DIR_NAME}/{FORM_INVENTORY_NAME})")
    parser.add_argument("--glossary-dir", help="glossary directory (default: <run>/25_glossary)")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    consistency = run_dir / CONSISTENCY_DIR_NAME
    output = Path(args.output) if args.output else consistency / FORM_INVENTORY_NAME
    consistency.mkdir(parents=True, exist_ok=True)

    cmd_line = " ".join(shlex.quote(arg) for arg in sys.argv)
    with (consistency / "terminology_form_inventory_command.txt").open("a", encoding="utf-8") as handle:
        handle.write(cmd_line + "\n")

    try:
        lemmas = load_candidate_lemmas(run_dir, args.review_csv)
        variants = load_sentence_variants(run_dir)
        glossary_dir = Path(args.glossary_dir) if args.glossary_dir else None
        rows = build_rows(run_dir, lemmas, LatinFormFinder(), variants, glossary_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    write_csv(
        output,
        ["lemma", "form", "occurrences", "sentences", "recorded_as_entry_source",
         "sentence_variants", "example_sentence_ids"],
        rows,
    )
    logger.info("Lemmas: %d, forms: %d", len(lemmas), len(rows))
    logger.info("Written: %s", output)


if __name__ == "__main__":
    main()
