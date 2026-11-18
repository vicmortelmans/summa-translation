#!/usr/bin/env python3
"""Plan a terminology harmonisation pass for one or more reviewed lemmas.

The input is the human-reviewed
``glossary-consistency/inter-batch-lemmata-disambiguation.csv`` (or its
``.reviewed.csv`` copy).  For every lemma row this script:

* finds the sentences whose glossary entry carries that lemma;
* enumerates every Latin surface form of the lemma in each sentence;
* maps the forms to the reviewed rows (form-specific rows override the
  lemma-wide default);
* classifies each sentence as ``conforming``, ``mixed`` or ``missing``;
* writes review requests for the sentences that need attention.

Requests are written to ``<run>/50-terminology/10_plan/requests/<lemma>/`` and
the planned decisions to ``<run>/50-terminology/10_plan/plan/<lemma>.csv``.
The script performs no API calls.

Usage::

    ./env/bin/python terminology_plan.py run-glossary --dry-run
    ./env/bin/python terminology_plan.py run-glossary --lemma accido1
"""

from __future__ import annotations

import argparse
import logging
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

from glossary_application_inventory import glossary_definitions
from terminology_common import (
    CONSISTENCY_DIR_NAME,
    TERMINOLOGY_DIR_NAME,
    TRANSLATION_DIR_NAME,
    ReviewRow,
    default_for_lemma,
    load_glossary_rows,
    load_review_rows,
    load_translations,
    other_definitions_present,
    resolve_review_csv,
    review_lemmas,
    rows_for_lemma,
    term_present,
    write_csv,
    write_json,
)
from terminology_forms import LatinFormFinder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PLAN_DIR_NAME = "10_plan"


@dataclass
class EntryRef:
    """One glossary entry of the target lemma in one sentence."""

    sentence_id: str
    batch: int
    source: str
    entry: dict


@dataclass
class FormGroup:
    """One preferred rendering and the forms it applies to."""

    preferred: str
    forms: list[str]
    note: str = ""


@dataclass
class LemmaReport:
    """Planning statistics for one lemma."""

    lemma: str
    sentences_with_lemma: int = 0
    selected_sentences: int = 0
    groups: int = 0
    conforming: int = 0
    mixed: int = 0
    missing: int = 0
    unknown_forms: list[str] = field(default_factory=list)
    uncovered_forms: list[str] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)



# ============================================================================
# Index and form grouping
# ============================================================================


def build_entry_index(glossary_rows) -> dict[str, list[EntryRef]]:
    """Index glossary entries by lemma."""
    index: dict[str, list[EntryRef]] = {}
    seen: dict[tuple[str, str], int] = {}
    for batch, row in glossary_rows:
        sentence_id = str(row["id"])
        for entry in row.get("glossary", []):
            lemma = str(entry.get("lemma", ""))
            if not lemma:
                continue
            key = (sentence_id, lemma)
            seen[key] = seen.get(key, 0) + 1
            index.setdefault(lemma, []).append(EntryRef(
                sentence_id=sentence_id,
                batch=batch,
                source=str(row.get("source", "")),
                entry=entry,
            ))
    ambiguous = sorted(key for key, count in seen.items() if count > 1)
    if ambiguous:
        logger.warning(
            "%d (sentence, lemma) pairs have multiple glossary entries, e.g. %s",
            len(ambiguous), ambiguous[:5],
        )
    return index


def entry_for(index: dict[str, list[EntryRef]], lemma: str, sentence_id: str) -> EntryRef | None:
    """Return the single glossary entry for a lemma in a sentence, if unambiguous."""
    matches = [ref for ref in index.get(lemma, []) if ref.sentence_id == sentence_id]
    if len(matches) == 1:
        return matches[0]
    return None


def validate_rows(rows: list[ReviewRow], lemma: str) -> None:
    """Validate the form rows of one lemma."""
    defaults = [row for row in rows if row.is_default]
    if len(defaults) > 1:
        raise ValueError(
            f"{lemma}: multiple default rows (lines {[row.line for row in defaults]}); "
            "only one row may leave 'forms' empty"
        )
    owner: dict[str, int] = {}
    for row in rows:
        for form in row.forms:
            key = form.lower()
            if key in owner and owner[key] != row.line:
                raise ValueError(
                    f"{lemma}: form {form!r} occurs on lines {owner[key]} and {row.line}"
                )
            owner[key] = row.line


def group_forms(forms: list[str], rows: list[ReviewRow]) -> tuple[list[FormGroup], list[str]]:
    """Map surface forms to the reviewed rows, returning groups and uncovered forms."""
    lookup: dict[str, ReviewRow] = {}
    default: ReviewRow | None = None
    for row in rows:
        if row.is_default:
            default = row
        for form in row.forms:
            lookup[form.lower()] = row
    grouped: dict[str, FormGroup] = {}
    uncovered: list[str] = []
    for form in forms:
        row = lookup.get(form.lower()) or default
        if row is None:
            uncovered.append(form)
            continue
        group = grouped.get(row.translation)
        if group is None:
            group = FormGroup(preferred=row.translation, forms=[], note=row.note)
            grouped[row.translation] = group
        group.forms.append(form)
    return list(grouped.values()), uncovered


def classify(preferred: str, definitions: list[str], translation: str) -> tuple[str, bool, bool]:
    """Classify one form group in a translation."""
    present = term_present(preferred, translation)
    other = other_definitions_present(definitions, preferred, translation)
    if present and other:
        return ("mixed", present, other)
    if present:
        return ("conforming", present, other)
    return ("missing", present, other)


def example_translations(
    index: dict[str, list[EntryRef]],
    lemma: str,
    preferred: str,
    translations: dict[str, str],
    limit: int,
) -> list[dict[str, str]]:
    """Return up to ``limit`` short sentences that already use the preferred term."""
    candidates: list[tuple[int, int, str]] = []
    for ref in index.get(lemma, []):
        translation = translations.get(ref.sentence_id, "")
        if translation and term_present(preferred, translation):
            candidates.append((len(translation), int(ref.sentence_id), translation))
    candidates.sort()
    return [{"translation": text} for _length, _sid, text in candidates[:limit]]


def locked_terms(
    lemmas: list[str],
    current: str,
    index: dict[str, list[EntryRef]],
    sentence_id: str,
    translation: str,
    preferred: dict[str, str],
) -> list[dict[str, str]]:
    """Return already harmonised terms of other lemmas in the same sentence."""
    locked: list[dict[str, str]] = []
    for lemma in lemmas:
        if lemma == current:
            continue
        ref = entry_for(index, lemma, sentence_id)
        term = preferred.get(lemma, "")
        if ref is None or not term:
            continue
        if term_present(term, translation):
            locked.append({"lemma": lemma, "translation": term})
    return locked


# ============================================================================
# Planning
# ============================================================================


def _safe_name(lemma: str) -> str:
    """Return a filesystem-safe directory name for a lemma."""
    return "".join(char if char.isalnum() or char in "-_." else "_" for char in lemma)


def plan_lemma(
    lemma: str,
    rows: list[ReviewRow],
    index: dict[str, list[EntryRef]],
    translations: dict[str, str],
    finder: LatinFormFinder,
    *,
    include_all: bool,
    example_limit: int,
    strict: bool,
    allow_unknown_forms: bool,
    preferred: dict[str, str],
    lemmas: list[str],
) -> tuple[list[list[str]], list[dict], LemmaReport]:
    """Build the plan rows and review items for one lemma."""
    report = LemmaReport(lemma=lemma)
    validate_rows(rows, lemma)

    refs = index.get(lemma, [])
    if not refs:
        raise ValueError(f"lemma {lemma!r} does not occur in the run's glossary")

    known: dict[str, str] = {}
    for ref in refs:
        for form in finder.surfaces(ref.source, ref.entry):
            known.setdefault(form.lower(), form)

    for row in rows:
        for form in row.forms:
            if form.lower() not in known:
                report.unknown_forms.append(form)
    if report.unknown_forms:
        message = (f"{lemma}: forms not found in this run: "
                   f"{', '.join(sorted(set(report.unknown_forms)))}")
        if strict:
            raise ValueError(message)
        if allow_unknown_forms:
            logger.info(message)
        else:
            logger.warning(message)

    examples: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if row.translation and row.translation not in examples:
            examples[row.translation] = example_translations(
                index, lemma, row.translation, translations, example_limit
            )
    items: list[dict] = []
    plan_rows: list[list[str]] = []
    uncovered: set[str] = set()

    for ref in refs:
        report.sentences_with_lemma += 1
        translation = translations.get(ref.sentence_id, "")
        if not translation:
            report.notes.append(f"no translation for sentence {ref.sentence_id}")
            continue
        forms = finder.surfaces(ref.source, ref.entry)
        if not forms:
            report.notes.append(f"no form found for sentence {ref.sentence_id}")
            continue
        groups, missing_forms = group_forms(forms, rows)
        if missing_forms:
            uncovered.update(missing_forms)
            report.notes.append(
                f"sentence {ref.sentence_id}: uncovered forms {', '.join(missing_forms)}"
            )
        if not groups:
            continue
        definitions = glossary_definitions(ref.entry)
        evaluated = []
        for group in groups:
            classification, present, other = classify(group.preferred, definitions, translation)
            evaluated.append((group, classification, present, other))
        selected = include_all or any(state != "conforming" for _g, state, _p, _o in evaluated)
        if not selected:
            continue
        report.selected_sentences += 1
        selected_groups = [
            entry for entry in evaluated
            if include_all or entry[1] != "conforming"
        ]
        report.groups += len(selected_groups)
        terms = []
        for group, classification, present, other in selected_groups:
            report.__dict__[classification] += 1
            action = "review" if classification != "conforming" else "skip"
            plan_rows.append([
                lemma,
                ref.sentence_id,
                str(ref.batch),
                ";".join(group.forms),
                group.preferred,
                classification,
                "yes" if present else "no",
                "yes" if other else "no",
                action,
                group.note,
            ])
            terms.append({
                "lemma": lemma,
                "forms": group.forms,
                "preferred": group.preferred,
                "definitions": definitions,
                "examples": examples.get(group.preferred, []),
            })
        if not terms:
            continue
        item: dict = {
            "id": ref.sentence_id,
            "source": ref.source,
            "translation": translation,
            "terms": terms,
        }
        locked = locked_terms(
            lemmas, lemma, index, ref.sentence_id, translation, preferred
        )
        if locked:
            item["locked_terms"] = locked
        items.append(item)

    if uncovered:
        report.uncovered_forms = sorted(uncovered)
        message = (f"{lemma}: sentences contain forms not covered by the review rows: "
                   f"{', '.join(report.uncovered_forms)}")
        if strict:
            raise ValueError(message)
        logger.warning(message)

    items.sort(key=lambda item: int(item["id"]))
    plan_rows.sort(key=lambda row: (int(row[1]), row[4]))
    return plan_rows, items, report


def write_requests(request_dir: Path, items: list[dict], chunk_size: int) -> list[Path]:
    """Write chunked request files and remove stale parts."""
    request_dir.mkdir(parents=True, exist_ok=True)
    for old in request_dir.glob("part*.json"):
        old.unlink()
    paths: list[Path] = []
    for index in range(0, len(items), chunk_size):
        path = request_dir / f"part{index // chunk_size + 1:02d}.json"
        write_json(path, items[index:index + chunk_size])
        paths.append(path)
    return paths


# ============================================================================
# Main
# ============================================================================


def resolve_layer(run_dir: Path, term_dir: Path, explicit: str | None) -> Path:
    """Return the translation layer to plan against."""
    if explicit:
        path = Path(explicit)
        if path.is_absolute():
            return path
        candidate = path.resolve()
        if candidate.exists():
            return candidate
        return (run_dir / path).resolve()
    corrected = term_dir / TRANSLATION_DIR_NAME
    if corrected.is_dir() and any(corrected.glob("*.json")):
        return corrected
    return run_dir / TRANSLATION_DIR_NAME


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan a terminology harmonisation pass from the reviewed CSV"
    )
    parser.add_argument("run_dir", nargs="?", default="run-glossary",
                        help="run directory (default: run-glossary)")
    parser.add_argument("--review-csv", help="reviewed disambiguation CSV")
    parser.add_argument("--lemma", action="append", default=None,
                        help="only plan this lemma (repeatable)")
    parser.add_argument("--layer", help="translation layer to plan against")
    parser.add_argument("--terminology-dir",
                        help=f"output root (default: <run>/{TERMINOLOGY_DIR_NAME})")
    parser.add_argument("--glossary-dir", help="glossary directory (default: <run>/25_glossary)")
    parser.add_argument("--sentences-per-request", type=int, default=50,
                        help="sentences per review request (default: 50)")
    parser.add_argument("--examples", type=int, default=2,
                        help="preferred-translation examples per item (default: 2)")
    parser.add_argument("--all", action="store_true",
                        help="also review sentences that already conform")
    parser.add_argument("--strict", action="store_true",
                        help="treat unknown or uncovered forms as errors")
    parser.add_argument("--allow-unknown-forms", action="store_true",
                        help="do not warn about reviewed forms absent from this run")
    parser.add_argument("--dry-run", "--validate-only", dest="dry_run", action="store_true",
                        help="validate the reviewed CSV and report counts without writing requests")
    args = parser.parse_args()

    if args.sentences_per_request < 1:
        parser.error("--sentences-per-request must be at least 1")
    if args.examples < 0:
        parser.error("--examples must not be negative")

    run_dir = Path(args.run_dir).resolve()
    term_dir = (Path(args.terminology_dir) if args.terminology_dir
                else run_dir / TERMINOLOGY_DIR_NAME)
    plan_dir = term_dir / PLAN_DIR_NAME
    plan_dir.mkdir(parents=True, exist_ok=True)

    cmd_line = " ".join(shlex.quote(arg) for arg in sys.argv)
    with (plan_dir / "terminology_plan_command.txt").open("a", encoding="utf-8") as handle:
        handle.write(cmd_line + "\n")

    try:
        review_path = resolve_review_csv(run_dir, args.review_csv)
        rows = load_review_rows(review_path)
        all_lemmas = review_lemmas(rows)
        lemmas = all_lemmas
        if args.lemma:
            unknown = [lemma for lemma in args.lemma if lemma not in all_lemmas]
            if unknown:
                raise ValueError(f"lemma/lemmas not in the review CSV: {', '.join(unknown)}")
            lemmas = [lemma for lemma in all_lemmas if lemma in set(args.lemma)]

        glossary_dir = Path(args.glossary_dir) if args.glossary_dir else None
        glossary_rows = load_glossary_rows(run_dir, glossary_dir)
        index = build_entry_index(glossary_rows)
        layer = resolve_layer(run_dir, term_dir, args.layer)
        translations = load_translations(layer)
        preferred = {lemma: default_for_lemma(rows, lemma) for lemma in all_lemmas}
        finder = LatinFormFinder()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    # Snapshot the reviewed CSV next to the plan so the decisions are auditable.
    import shutil
    shutil.copyfile(review_path, plan_dir / "reviewed.csv")


    reports: list[LemmaReport] = []
    try:
        for lemma in lemmas:
            plan_rows, items, report = plan_lemma(
                lemma,
                rows_for_lemma(rows, lemma),
                index,
                translations,
                finder,
                include_all=args.all,
                example_limit=args.examples,
                strict=args.strict,
                allow_unknown_forms=args.allow_unknown_forms,
                preferred=preferred,
                lemmas=all_lemmas,
            )
            reports.append(report)
            write_csv(
                plan_dir / "plan" / f"{_safe_name(lemma)}.csv",
                ["lemma", "sentence_id", "batch", "forms", "preferred",
                 "classification", "preferred_present", "other_present",
                 "action", "note"],
                plan_rows,
            )
            write_json(plan_dir / "summary" / f"{_safe_name(lemma)}.json", {
                "lemma": lemma,
                "reviewed_forms": [form for row in rows_for_lemma(rows, lemma)
                                   for form in row.forms],
                "sentences_with_lemma": report.sentences_with_lemma,
                "selected_sentences": report.selected_sentences,
                "term_groups": report.groups,
                "conforming": report.conforming,
                "mixed": report.mixed,
                "missing": report.missing,
                "unknown_forms": sorted(set(report.unknown_forms)),
                "uncovered_forms": report.uncovered_forms,
                "notes": report.notes,
            })
            if not args.dry_run:
                request_dir = plan_dir / "requests" / _safe_name(lemma)
                paths = write_requests(request_dir, items, args.sentences_per_request)
                report.requests = [str(path) for path in paths]
            logger.info(
                "%s: %d sentences, %d selected, %d groups "
                "(conforming %d, mixed %d, missing %d), %d requests%s",
                lemma,
                report.sentences_with_lemma,
                report.selected_sentences,
                report.groups,
                report.conforming,
                report.mixed,
                report.missing,
                len(report.requests),
                " (dry run)" if args.dry_run else "",
            )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    write_json(plan_dir / "summary.json", {
        "run_dir": str(run_dir),
        "review_csv": str(review_path),
        "layer": str(layer),
        "dry_run": args.dry_run,
        "lemmas": [
            {
                "lemma": report.lemma,
                "sentences_with_lemma": report.sentences_with_lemma,
                "selected_sentences": report.selected_sentences,
                "term_groups": report.groups,
                "conforming": report.conforming,
                "mixed": report.mixed,
                "missing": report.missing,
                "unknown_forms": sorted(set(report.unknown_forms)),
                "uncovered_forms": report.uncovered_forms,
                "requests": report.requests,
                "notes": report.notes,
            }
            for report in reports
        ],
    })
    logger.info("Plan written to: %s", plan_dir)


if __name__ == "__main__":
    main()

