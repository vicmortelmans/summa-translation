#!/usr/bin/env python3
"""Shared helpers for the terminology harmonisation workflow.

This module is used by ``terminology_form_inventory.py``,
``terminology_plan.py``, ``terminology_apply.py`` and
``terminology_verify.py``.  It contains no CLTK imports at module level so the
post-processing scripts stay lightweight; the lemmatizer is loaded lazily by
:class:`LatinFormFinder`.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from glossary_application_inventory import (
    ARTICLE_PREFIXES,
    STOPWORDS,
    WORD_RE,
    fold,
    glossary_definitions,
)

GLOSSARY_DIR_NAME = "25_glossary"
TRANSLATION_DIR_NAME = "30_translation"
CONSISTENCY_DIR_NAME = "glossary-consistency"
TERMINOLOGY_DIR_NAME = "50-terminology"

REVIEW_FILE_NAME = "inter-batch-lemmata-disambiguation.csv"
REVIEWED_FILE_NAME = "inter-batch-lemmata-disambiguation.reviewed.csv"
FORM_INVENTORY_NAME = "lemma_form_inventory.csv"

BATCH_RE = re.compile(r"batch(\d+)")


# ============================================================================
# Reviewed CSV
# ============================================================================


@dataclass(frozen=True)
class ReviewRow:
    """One row of the reviewed disambiguation CSV."""

    line: int
    lemma: str
    translation: str
    forms: tuple[str, ...]
    note: str = ""

    @property
    def is_default(self) -> bool:
        return not self.forms


def load_review_rows(path: Path) -> list[ReviewRow]:
    """Read the reviewed disambiguation CSV.

    Required columns are ``lemma`` and ``translation``; ``forms`` and ``note``
    are optional.  The ``forms`` column is a semicolon-separated list of Latin
    surface forms.  An empty ``forms`` value marks a lemma-wide default row.
    Rows without a lemma are ignored; rows without a translation mark a lemma
    (or form group) that must be skipped.
    """
    rows: list[ReviewRow] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = {name.strip().lower() for name in (reader.fieldnames or [])}
        missing = {"lemma", "translation"} - fieldnames
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        for line, raw in enumerate(reader, start=2):
            lemma = (raw.get("lemma") or "").strip()
            if not lemma:
                continue
            translation = (raw.get("translation") or "").strip()
            forms_raw = (raw.get("forms") or "").strip()
            forms = tuple(
                part.strip() for part in forms_raw.replace(",", ";").split(";")
                if part.strip()
            )
            rows.append(ReviewRow(
                line=line,
                lemma=lemma,
                translation=translation,
                forms=forms,
                note=(raw.get("note") or "").strip(),
            ))
    if not rows:
        raise ValueError(f"{path}: no review rows")
    return rows


def review_lemmas(rows: Sequence[ReviewRow]) -> list[str]:
    """Return the lemmas in first-appearance order, skipping fully blank rows."""
    result: list[str] = []
    for row in rows:
        if not row.translation:
            continue
        if row.lemma not in result:
            result.append(row.lemma)
    return result


def rows_for_lemma(rows: Sequence[ReviewRow], lemma: str) -> list[ReviewRow]:
    """Return the non-blank rows of one lemma, in file order."""
    return [row for row in rows if row.lemma == lemma and row.translation]


def default_for_lemma(rows: Sequence[ReviewRow], lemma: str) -> str:
    """Return the lemma-wide default translation (first non-blank row)."""
    candidates = rows_for_lemma(rows, lemma)
    defaults = [row.translation for row in candidates if row.is_default]
    if defaults:
        return defaults[0]
    return candidates[0].translation if candidates else ""


def resolve_review_csv(run_dir: Path, explicit: str | None = None) -> Path:
    """Resolve the reviewed CSV, preferring the ``.reviewed.csv`` copy."""
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise ValueError(f"review CSV not found: {path}")
        return path
    consistency = run_dir / CONSISTENCY_DIR_NAME
    for name in (REVIEWED_FILE_NAME, REVIEW_FILE_NAME):
        candidate = consistency / name
        if candidate.is_file():
            return candidate
    raise ValueError(
        f"no review CSV found in {consistency} "
        f"(expected {REVIEWED_FILE_NAME} or {REVIEW_FILE_NAME})"
    )


# ============================================================================
# Run layout
# ============================================================================


def batch_number(path: Path) -> int | None:
    """Return the ``batch<N>`` number of a run file, if present."""
    match = BATCH_RE.search(path.name)
    return int(match.group(1)) if match else None


def load_glossary_rows(run_dir: Path, glossary_dir: Path | None = None) -> list[tuple[int, dict]]:
    """Load ``(batch, row)`` pairs from the run's glossary directory."""
    directory = glossary_dir or (run_dir / GLOSSARY_DIR_NAME)
    rows: list[tuple[int, dict]] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        batch = batch_number(path)
        if batch is None:
            continue
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        for row in payload:
            identifier = str(row["id"])
            if identifier in seen:
                raise ValueError(f"duplicate glossary id {identifier} in {path}")
            seen.add(identifier)
            rows.append((batch, row))
    if not rows:
        raise ValueError(f"no glossary rows found in {directory}")
    rows.sort(key=lambda item: int(item[1]["id"]))
    return rows


def load_translation_files(directory: Path) -> list[tuple[Path, dict[str, str]]]:
    """Load every translation file as ``(path, {id: translation})``."""
    result: list[tuple[Path, dict[str, str]]] = []
    for path in sorted(directory.glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        records = payload.get("translations") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise ValueError(f"{path}: no 'translations' list")
        mapping: dict[str, str] = {}
        for item in records:
            identifier = str(item["id"])
            if identifier in mapping:
                raise ValueError(f"duplicate translation id {identifier} in {path}")
            mapping[identifier] = item["translation"]
        result.append((path, mapping))
    if not result:
        raise ValueError(f"no translation files found in {directory}")
    return result


def load_translations(directory: Path) -> dict[str, str]:
    """Load ``id -> translation`` from a translation directory."""
    merged: dict[str, str] = {}
    for _path, mapping in load_translation_files(directory):
        for identifier, translation in mapping.items():
            if identifier in merged:
                raise ValueError(f"duplicate translation id {identifier}")
            merged[identifier] = translation
    return merged


def write_json(path: Path, payload: object) -> None:
    """Write JSON with the project's formatting conventions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[str]]) -> None:
    """Write a UTF-8 CSV with the project's line terminator."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(list(header))
        writer.writerows(rows)


# ============================================================================
# Text matching
# ============================================================================


def tokenize(text: str) -> list[str]:
    """Tokenize Dutch text the same way the inventory does."""
    return WORD_RE.findall(text)


STRICT_SUFFIXES = ("", "e", "en", "n", "s", "er", "ere", "st", "ste")
VOWEL_SUFFIXES = ("e", "en", "er", "ere", "st", "ste")


def strict_variants(term: str) -> frozenset[str]:
    """Return inflections of a full word, without derivational stems.

    The inventory's ``variants()`` also accepts derived forms such as
    ``gelukzalige`` for ``gelukzaligheid`` (via the ``-heid`` root).  For the
    terminology review that is too loose: ``de gelukzalige`` means "the blessed
    one", not "blessedness".  This matcher only inflects the whole word.
    """
    term = fold(term.strip())
    out = {term}
    out.update(term + suffix for suffix in STRICT_SUFFIXES)
    for long_vowel in ("aa", "ee", "oo", "uu"):
        if long_vowel in term:
            collapsed = term.replace(long_vowel, long_vowel[0])
            out.update(collapsed + suffix for suffix in VOWEL_SUFFIXES)
    if term.endswith("heid") and len(term) > 5:
        out.update({term[:-4] + "heden", term[:-1] + "s"})
    if term.endswith("ing") and len(term) > 4:
        out.update({term + "en", term + "s"})
    return frozenset(out)


def strict_match_token(word: str, accepted: frozenset[str]) -> bool:
    """Return whether a token is an inflection of the whole term."""
    lower = fold(word)
    if lower in accepted:
        return True
    for prefix in ARTICLE_PREFIXES:
        if lower.startswith(prefix) and len(lower) > len(prefix) + 2:
            if lower[len(prefix):] in accepted:
                return True
    for part in re.split(r"[-'’]", lower):
        if part and part in accepted:
            return True
    return False


def term_present(term: str, text: str) -> bool:
    """Return whether ``term`` (possibly a phrase) occurs in ``text``."""
    if not term:
        return False
    words = tokenize(text)
    parts = [fold(part) for part in term.split() if part.strip()]
    if not parts:
        return False
    if len(parts) == 1:
        accepted = strict_variants(parts[0])
        return any(strict_match_token(word, accepted) for word in words)
    for start in range(len(words) - len(parts) + 1):
        for offset, part in enumerate(parts):
            word = words[start + offset]
            matched = (fold(word) == part if part in STOPWORDS
                       else strict_match_token(word, strict_variants(part)))
            if not matched:
                break
        else:
            return True
    # Fallback: all content words occur somewhere in the sentence.
    for part in parts:
        if part in STOPWORDS:
            if not any(fold(word) == part for word in words):
                return False
            continue
        accepted = strict_variants(part)
        if not any(strict_match_token(word, accepted) for word in words):
            return False
    return True


def count_term(term: str, text: str) -> int:
    """Approximate the number of occurrences of ``term`` in ``text``."""
    if not term:
        return 0
    parts = [fold(part) for part in term.split() if part.strip()]
    content = [part for part in parts if part not in STOPWORDS] or parts
    accepted = [strict_variants(part) for part in content]
    return sum(
        1 for word in tokenize(text)
        if any(strict_match_token(word, variants) for variants in accepted)
    )


def content_words(term: str) -> frozenset[str]:
    """Return the content words of a term, ignoring articles and prepositions."""
    return frozenset(
        fold(part) for part in term.split()
        if part.strip() and fold(part) not in STOPWORDS
    )


def same_term(left: str, right: str) -> bool:
    """Return whether two renderings denote the same term (ignoring articles)."""
    left_words = content_words(left)
    return bool(left_words) and left_words == content_words(right)


def other_definitions_present(definitions: Sequence[str], preferred: str, text: str) -> bool:
    """Return whether a genuinely different glossary alternative occurs in ``text``."""
    for definition in definitions:
        if not definition or same_term(definition, preferred):
            continue
        if term_present(definition, text):
            return True
    return False
