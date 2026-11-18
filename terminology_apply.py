#!/usr/bin/env python3
"""Apply model terminology revisions to a translation layer.

The script reads the plan and the model revisions of one or more lemmas,
validates that exactly the planned sentences were revised, writes a corrected
translation layer, and produces an auditable change report.

Outputs (default under ``<run>/50-terminology``)::

    30_translation/batch<N>_<tag>.json     corrected layer, original filenames
    30_translation/tokens/                 base tokens + revision tokens
    40_changes/changes.csv                 one row per sentence x term
    40_changes/exceptions.csv              terms the model declined to apply
    40_changes/summary/<lemma>.json
    40_changes/report.md

Usage::

    ./env/bin/python terminology_apply.py run-glossary --lemma beatitudo
"""

from __future__ import annotations

import argparse
import csv
import difflib
import logging
import shlex
import shutil
import sys
from pathlib import Path

from terminology_common import (
    TERMINOLOGY_DIR_NAME,
    TRANSLATION_DIR_NAME,
    count_term,
    term_present,
    tokenize,
    write_csv,
    write_json,
)
from terminology_plan import _safe_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PLAN_DIR_NAME = "10_plan"
REVIEW_DIR_NAME = "20_review"
CHANGES_DIR_NAME = "40_changes"


def read_csv_rows(path: Path) -> list[dict]:
    """Read a CSV file into dictionaries."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_plan(plan_dir: Path, lemma: str) -> list[dict]:
    """Load the plan rows of one lemma."""
    path = plan_dir / "plan" / f"{_safe_name(lemma)}.csv"
    if not path.is_file():
        raise ValueError(f"plan not found: {path}")
    rows = read_csv_rows(path)
    if not rows:
        raise ValueError(f"plan is empty: {path}")
    return rows


def load_requests(plan_dir: Path, lemma: str) -> list[dict]:
    """Load the request items of one lemma, in part order."""
    directory = plan_dir / "requests" / _safe_name(lemma)
    paths = sorted(directory.glob("part*.json"))
    if not paths:
        raise ValueError(f"no request files found in {directory}")
    items: list[dict] = []
    for path in paths:
        import json
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, list):
            raise ValueError(f"{path}: request must be a JSON array")
        items.extend(payload)
    return items


def load_revisions(review_dir: Path, lemma: str) -> dict[str, dict]:
    """Load the model revisions of one lemma by sentence id."""
    directory = review_dir / _safe_name(lemma)
    paths = sorted(directory.glob("part*.json"))
    if not paths:
        raise ValueError(f"no revision files found in {directory}")
    revisions: dict[str, dict] = {}
    for path in paths:
        import json
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        records = payload.get("translations") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise ValueError(f"{path}: no 'translations' list")
        for record in records:
            identifier = str(record.get("id", ""))
            if not identifier:
                raise ValueError(f"{path}: revision without an id")
            if identifier in revisions:
                raise ValueError(f"{path}: duplicate revision id {identifier}")
            revisions[identifier] = record
    return revisions


def change_ratio(before: str, after: str) -> float:
    """Return the fraction of word tokens changed between two sentences."""
    old = [word.lower() for word in tokenize(before)]
    new = [word.lower() for word in tokenize(after)]
    if not old and not new:
        return 0.0
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    return 1.0 - matcher.ratio()


def verify_revision(
    item: dict,
    record: dict,
    *,
    max_change_ratio: float,
    check_counts: bool,
) -> tuple[list[dict], list[str]]:
    """Validate one revision and return its change rows and flags."""
    before = item.get("translation", "")
    after = str(record.get("translation", ""))
    results = record.get("terms")
    terms = item.get("terms", [])
    if not isinstance(results, list) or len(results) != len(terms):
        raise ValueError(
            f"sentence {item['id']}: expected {len(terms)} term results, "
            f"got {len(results) if isinstance(results, list) else 'none'}"
        )
    ratio = change_ratio(before, after)
    flags: list[str] = []
    if ratio > max_change_ratio:
        flags.append("large_change")

    locked = item.get("locked_terms", [])
    missing_locked = [
        pair["lemma"] for pair in locked
        if not term_present(pair.get("translation", ""), after)
    ]
    if missing_locked:
        flags.append("locked_term_missing:" + ",".join(missing_locked))

    rows: list[dict] = []
    for term, result in zip(terms, results):
        if result.get("lemma") != term.get("lemma"):
            raise ValueError(
                f"sentence {item['id']}: term order mismatch "
                f"({term.get('lemma')} != {result.get('lemma')})"
            )
        applied = bool(result.get("applied"))
        rendering = str(result.get("rendering", "")).strip()
        preferred = str(term.get("preferred", ""))
        reason = str(result.get("reason", "")).strip()
        term_flags = list(flags)
        if applied:
            if not rendering:
                rendering = preferred
                term_flags.append("empty_rendering")
            if not term_present(preferred, after):
                term_flags.append("preferred_missing_after")
            if rendering and not term_present(rendering, after):
                term_flags.append("rendering_missing_after")
            verification = "verified"
            if any(flag.endswith("_after") or flag == "empty_rendering"
                   for flag in term_flags):
                verification = "unverified"
        else:
            verification = "kept"
            if after != before:
                term_flags.append("changed_but_not_applied")
                verification = "unverified"
        dutch_matches = count_term(rendering or preferred, after)
        if check_counts and applied and dutch_matches < len(term.get("forms", [])):
            term_flags.append("under_rendered")
        rows.append({
            "lemma": term.get("lemma", ""),
            "sentence_id": str(item["id"]),
            "applied": "yes" if applied else "no",
            "rendering": rendering,
            "preferred": preferred,
            "reason": reason,
            "verification": verification,
            "change_ratio": f"{ratio:.4f}",
            "dutch_matches": str(dutch_matches),
            "flags": ";".join(dict.fromkeys(term_flags)),
            "before": before,
            "after": after,
        })
    return rows, flags


def apply_to_layer(
    input_layer: Path,
    output_layer: Path,
    revisions: dict[str, dict],
) -> None:
    """Write ``input_layer`` to ``output_layer`` with revised translations."""
    output_layer.mkdir(parents=True, exist_ok=True)
    remaining = set(revisions)
    for path in sorted(input_layer.glob("*.json")):
        import json
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        records = payload.get("translations") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise ValueError(f"{path}: no 'translations' list")
        updated = []
        for record in records:
            identifier = str(record["id"])
            if identifier in revisions:
                remaining.discard(identifier)
                updated.append({
                    "id": identifier,
                    "translation": str(revisions[identifier]["translation"]),
                })
            else:
                updated.append({"id": identifier, "translation": record["translation"]})
        write_json(output_layer / path.name, {"translations": updated})
    if remaining:
        raise ValueError(
            f"revisions for unknown sentence ids: {sorted(remaining)[:5]}"
        )
    token_source = input_layer / "tokens"
    token_target = output_layer / "tokens"
    if token_source.is_dir() and token_source.resolve() != token_target.resolve():
        token_target.mkdir(parents=True, exist_ok=True)
        for path in token_source.glob("*.json"):
            shutil.copyfile(path, token_target / path.name)


def copy_revision_tokens(review_dir: Path, lemma: str, output_layer: Path) -> None:
    """Copy revision token files into the corrected layer with unique names."""
    source = review_dir / _safe_name(lemma) / "tokens"
    if not source.is_dir():
        return
    target = output_layer / "tokens"
    target.mkdir(parents=True, exist_ok=True)
    for path in source.glob("*.json"):
        shutil.copyfile(path, target / f"terminology_{_safe_name(lemma)}_{path.name}")



CHANGE_COLUMNS = [
    "lemma", "sentence_id", "applied", "rendering", "preferred", "reason",
    "verification", "change_ratio", "dutch_matches", "flags", "before", "after",
]
EXCEPTION_COLUMNS = ["lemma", "sentence_id", "rendering", "reason", "before", "after"]


def aggregate(changes_dir: Path, name: str, columns: list[str], *, exceptions: bool) -> None:
    """Rebuild an aggregate CSV from the per-lemma files."""
    rows: list[list[str]] = []
    for path in sorted(changes_dir.glob("*.csv")):
        if path.name in {"changes.csv", "exceptions.csv"}:
            continue
        is_exception_file = path.name.startswith("exceptions-")
        if is_exception_file != exceptions:
            continue
        for row in read_csv_rows(path):
            rows.append([row.get(column, "") for column in columns])
    write_csv(changes_dir / name, columns, rows)


def write_report(changes_dir: Path) -> None:
    """Write a compact Markdown report from the per-lemma summaries."""
    import json
    summaries = []
    for path in sorted((changes_dir / "summary").glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            summaries.append(json.load(handle))
    lines = ["# Terminology harmonisation report", ""]
    if not summaries:
        lines.append("No lemmas applied yet.")
    else:
        lines.extend([
            "| lemma | planned | applied | kept | unverified | large changes |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ])
        for item in summaries:
            lines.append(
                f"| **{item['lemma']}** | {item['planned']} | {item['applied']} "
                f"| {item['kept']} | {item['unverified']} | {item['large_changes']} |"
            )
    lines.extend([
        "",
        "Details: `changes.csv` (every sentence x term), `exceptions.csv` "
        "(terms kept with a reason), `summary/` (per-lemma counters).",
        "",
        "`unverified` rows need human inspection: the revised sentence does not "
        "contain the preferred rendering, the model changed a sentence it did not "
        "apply, or a locked term disappeared.",
        "",
    ])
    (changes_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply terminology revisions to a translation layer"
    )
    parser.add_argument("run_dir", nargs="?", default="run-glossary")
    parser.add_argument("--lemma", action="append", default=None,
                        help="lemma to apply (repeatable; default: every planned lemma)")
    parser.add_argument("--terminology-dir",
                        help=f"terminology root (default: <run>/{TERMINOLOGY_DIR_NAME})")
    parser.add_argument("--plan-dir", help="plan directory (default: <term>/10_plan)")
    parser.add_argument("--revision-dir", help="revision directory (default: <term>/20_review)")
    parser.add_argument("--input-layer",
                        help="translation layer to read (default: <run>/30_translation)")
    parser.add_argument("--output-layer",
                        help="corrected layer to write (default: <term>/30_translation)")
    parser.add_argument("--changes-dir", help="report directory (default: <term>/40_changes)")
    parser.add_argument("--max-change-ratio", type=float, default=0.5,
                        help="flag sentences whose word change ratio exceeds this (default: 0.5)")
    parser.add_argument("--no-count-check", action="store_true",
                        help="do not flag possible under-rendering")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    term_dir = (Path(args.terminology_dir) if args.terminology_dir
                else run_dir / TERMINOLOGY_DIR_NAME)
    plan_dir = Path(args.plan_dir) if args.plan_dir else term_dir / PLAN_DIR_NAME
    review_dir = (Path(args.revision_dir) if args.revision_dir
                  else term_dir / REVIEW_DIR_NAME)
    input_layer = (Path(args.input_layer) if args.input_layer
                   else run_dir / TRANSLATION_DIR_NAME)
    output_layer = (Path(args.output_layer) if args.output_layer
                    else term_dir / TRANSLATION_DIR_NAME)
    changes_dir = (Path(args.changes_dir) if args.changes_dir
                   else term_dir / CHANGES_DIR_NAME)
    changes_dir.mkdir(parents=True, exist_ok=True)

    cmd_line = " ".join(shlex.quote(arg) for arg in sys.argv)
    with (changes_dir / "terminology_apply_command.txt").open("a", encoding="utf-8") as handle:
        handle.write(cmd_line + "\n")

    lemmas = args.lemma
    if not lemmas:
        lemmas = []
        for path in sorted((plan_dir / "plan").glob("*.csv")):
            rows = read_csv_rows(path)
            if rows and rows[0].get("lemma") and rows[0]["lemma"] not in lemmas:
                lemmas.append(rows[0]["lemma"])
    if not lemmas:
        print("No planned lemmas found.", file=sys.stderr)
        sys.exit(1)

    all_revisions: dict[str, dict] = {}
    all_changes: list[dict] = []
    all_exceptions: list[dict] = []


    try:
        for lemma in lemmas:
            plan_rows = load_plan(plan_dir, lemma)
            items = load_requests(plan_dir, lemma)
            revisions = load_revisions(review_dir, lemma)
            expected = {str(item["id"]) for item in items}
            if set(revisions) != expected:
                missing = sorted(expected - set(revisions))
                extra = sorted(set(revisions) - expected)
                raise ValueError(
                    f"{lemma}: revision ids do not match the plan "
                    f"(missing {missing[:5]}, extra {extra[:5]})"
                )
            lemma_changes: list[dict] = []
            for item in items:
                rows, _flags = verify_revision(
                    item,
                    revisions[str(item["id"])],
                    max_change_ratio=args.max_change_ratio,
                    check_counts=not args.no_count_check,
                )
                lemma_changes.extend(rows)
                sentence_id = str(item["id"])
                if sentence_id in all_revisions:
                    raise ValueError(
                        f"sentence {sentence_id} is revised for more than one lemma; "
                        "run terminology_apply.py once per lemma (as the workflow does)"
                    )
                all_revisions[sentence_id] = revisions[sentence_id]
            exceptions = [
                {key: row[key] for key in EXCEPTION_COLUMNS}
                for row in lemma_changes if row["applied"] == "no"
            ]
            all_changes.extend(lemma_changes)
            all_exceptions.extend(exceptions)
            write_csv(
                changes_dir / f"{_safe_name(lemma)}.csv",
                CHANGE_COLUMNS,
                [[row[column] for column in CHANGE_COLUMNS] for row in lemma_changes],
            )
            write_csv(
                changes_dir / f"exceptions-{_safe_name(lemma)}.csv",
                EXCEPTION_COLUMNS,
                [[row[column] for column in EXCEPTION_COLUMNS] for row in exceptions],
            )
            summary = {
                "lemma": lemma,
                "planned": len(plan_rows),
                "sentences": len({row["sentence_id"] for row in plan_rows}),
                "applied": sum(1 for row in lemma_changes if row["applied"] == "yes"),
                "kept": sum(1 for row in lemma_changes if row["applied"] == "no"),
                "unverified": sum(1 for row in lemma_changes
                                  if row["verification"] == "unverified"),
                "large_changes": sum(1 for row in lemma_changes
                                     if "large_change" in row["flags"]),
                "under_rendered": sum(1 for row in lemma_changes
                                      if "under_rendered" in row["flags"]),
            }
            write_json(changes_dir / "summary" / f"{_safe_name(lemma)}.json", summary)
            logger.info(
                "%s: %d sentences, %d applied, %d kept, %d unverified",
                lemma, summary["sentences"], summary["applied"], summary["kept"],
                summary["unverified"],
            )

        apply_to_layer(input_layer, output_layer, all_revisions)
        for lemma in lemmas:
            copy_revision_tokens(review_dir, lemma, output_layer)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    aggregate(changes_dir, "changes.csv", CHANGE_COLUMNS, exceptions=False)
    aggregate(changes_dir, "exceptions.csv", EXCEPTION_COLUMNS, exceptions=True)
    write_report(changes_dir)
    logger.info("Corrected layer: %s", output_layer)
    logger.info("Changes: %d, exceptions: %d", len(all_changes), len(all_exceptions))
    logger.info("Report: %s", changes_dir / "report.md")


if __name__ == "__main__":
    main()

