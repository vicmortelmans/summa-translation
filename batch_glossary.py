#!/usr/bin/env python3
"""Add glossary entries to a batch of sentences.

The script reads a batch JSON file (a list of objects that each contain a
``source`` sentence), lemmatizes every word of the sentence with CLTK's
``LatinBackoffLemmatizer`` and looks the lemma up in the Axters scholastic
lexicon.  Matches are added to the input objects under a ``glossary`` field:
a list of objects with the word as it appears in the sentence (``source``),
its lemmatized form (``lemma``) and an ordered list of Dutch definitions from
the lexicon (``definitions``).  A word that is an exact lexicon headword uses
that entry only; when a CLTK lemma matches several lexicon entries without an
exact headword match, their definitions are merged into one ordered list.

Output is written to ``<job name>/25_glossary/<input file name>``, where the
job name is the parent directory of the directory containing the input file
(e.g. ``run-test3/20_references/translation_input_batch1_test3.json`` is
written to ``run-test3/25_glossary/translation_input_batch1_test3.json``).

Usage:
    ./env/bin/python batch_glossary.py run-test3/20_references/translation_input_batch1_test3.json
"""

import json
import argparse
import re
import sys
import shlex
from pathlib import Path
from typing import Dict, List

from cltk.lemmatize.lat import LatinBackoffLemmatizer

import logging

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
LEXICON_PATH = BASE_DIR / "axters-mistral" / "axters-scholastiek-lexicon-1937-cltk.json"
GLOSSARY_DIR_NAME = "25_glossary"
SCRIPT_STEM = Path(__file__).stem

# Latin words may contain letters only; punctuation and digits are not part
# of the word itself.
WORD_PATTERN = re.compile(r"[^\W\d_]+", re.UNICODE)
PARENTHETICAL_PATTERN = re.compile(r"\([^)]*\)")


# ============================================================================
# Lexicon
# ============================================================================


def normalize_key(value: str) -> str:
    """Normalize a lexicon key or lemma for case-insensitive lookup."""
    value = PARENTHETICAL_PATTERN.sub(" ", value or "")
    return " ".join(value.lower().split())


def split_definition(definition: str) -> List[str]:
    """Split a lexicon definition into its separate senses."""
    return [part.strip() for part in definition.split(",") if part.strip()]


def merge_definitions(definitions: List[str]) -> List[str]:
    """Merge definition lists by interleaving them round-robin.

    The shortest list comes first, so ``["a, b, c", "d, e"]`` becomes
    ``["d", "a", "e", "b", "c"]``.  Equal-length lists keep their given order
    (lexicon file order).  Duplicate senses are dropped case-insensitively,
    keeping the first spelling.
    """
    lists = [split_definition(definition) for definition in definitions]
    lists = [parts for parts in lists if parts]
    if not lists:
        return []
    if len(lists) == 1:
        return lists[0]

    lists.sort(key=len)  # stable: shortest first, ties in lexicon order

    merged: List[str] = []
    seen: set[str] = set()
    for index in range(max(len(parts) for parts in lists)):
        for parts in lists:
            if index >= len(parts):
                continue
            sense = parts[index]
            key = sense.lower()
            if key not in seen:
                seen.add(key)
                merged.append(sense)

    return merged


def load_lexicon(path: Path) -> tuple[Dict[str, List[dict]], Dict[str, dict]]:
    """Load the Axters lexicon and return indexes by CLTK lemma and by lemma.

    ``by_cltk`` maps a CLTK lemma to *all* lexicon entries that share it, so
    that ambiguous CLTK lemmas can be merged.  ``by_lemma`` maps a lexicon
    headword to its single entry and is used for exact matches.
    """
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Lexicon file '{path}' does not exist.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"Lexicon file '{path}' is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(entries, list):
        print(f"Lexicon file '{path}' must contain a JSON array.", file=sys.stderr)
        sys.exit(1)

    by_cltk: Dict[str, List[dict]] = {}
    by_lemma: Dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict) or "definition" not in entry:
            continue

        cltk_key = normalize_key(entry.get("cltk", ""))
        if cltk_key:
            by_cltk.setdefault(cltk_key, []).append(entry)

        lemma_key = normalize_key(entry.get("lemma", ""))
        if lemma_key:
            by_lemma.setdefault(lemma_key, entry)

    return by_cltk, by_lemma


# ============================================================================
# Glossary
# ============================================================================


def resolve_entry(
    source: str,
    lemma: str,
    by_cltk: Dict[str, List[dict]],
    by_lemma: Dict[str, dict],
) -> tuple[str, str, List[dict]] | None:
    """Resolve a word to an exact lexicon entry or to a group of entries.

    Returns ``(kind, lemma_key, entries)`` where ``kind`` is ``"exact"`` when
    the word or its lemma is a lexicon headword, and ``"group"`` when only the
    CLTK lemma (or, as a last resort, the surface form) matched one or more
    entries.  Returns ``None`` when nothing matches.
    """
    lemma_key = normalize_key(lemma)
    surface_key = normalize_key(source)

    # An exact headword match wins; the word as it appears in the sentence
    # takes precedence over its lemmatized form.
    for key in (surface_key, lemma_key):
        if key and key in by_lemma:
            return ("exact", lemma_key, [by_lemma[key]])

    # Otherwise fall back to the CLTK lemma, then to the surface form.
    for key in (lemma_key, surface_key):
        if key and key in by_cltk:
            return ("group", lemma_key, list(by_cltk[key]))

    return None


def build_glossary(
    sentence: str,
    lemmatizer: LatinBackoffLemmatizer,
    by_cltk: Dict[str, List[dict]],
    by_lemma: Dict[str, dict],
    lemma_cache: Dict[str, str],
) -> List[dict]:
    """Return the glossary entries found in a single sentence.

    Each entry contains the word as it appears in the sentence (``source``),
    its lemmatized form (``lemma``) and an ordered list of Dutch definitions
    (``definitions``).

    A word that is an exact lexicon headword uses that entry only.  A word that
    matches only a CLTK lemma shared by several lexicon entries gets the merged
    definitions of all of them, because none of them is primary.  An exact
    headword match suppresses the merged entry for the same CLTK lemma within
    the sentence.  Duplicate terms are dropped, preserving the order in which
    the words first appear.
    """
    tokens = WORD_PATTERN.findall(sentence)
    if not tokens:
        return []

    lowered = [token.lower() for token in tokens]
    unknown = [token for token in lowered if token not in lemma_cache]
    if unknown:
        for token, lemma in lemmatizer.lemmatize(unknown):
            lemma_cache[token] = lemma

    resolutions: List[tuple[str, str, str, str, List[dict]]] = []
    for source, token in zip(tokens, lowered):
        lemma = lemma_cache.get(token, token)
        resolved = resolve_entry(source, lemma, by_cltk, by_lemma)
        if resolved is None:
            continue
        kind, lemma_key, entries = resolved
        resolutions.append((source, lemma, kind, lemma_key, entries))

    exact_lemmas = {
        lemma_key
        for _source, _lemma, kind, lemma_key, _entries in resolutions
        if kind == "exact"
    }

    glossary: List[dict] = []
    seen: set[tuple[str, ...]] = set()
    for source, lemma, kind, lemma_key, entries in resolutions:
        if not lemma_key:
            continue

        if kind == "exact":
            entry = entries[0]
            dedupe_key = ("exact", lemma_key, normalize_key(entry.get("lemma", "")))
            definitions = split_definition(entry["definition"])
        else:
            if lemma_key in exact_lemmas:
                continue
            dedupe_key = ("group", lemma_key)
            definitions = (
                split_definition(entries[0]["definition"])
                if len(entries) == 1
                else merge_definitions([entry["definition"] for entry in entries])
            )

        if not definitions or dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        glossary.append(
            {
                "source": source,
                "lemma": lemma,
                "definitions": definitions,
            }
        )

    return glossary


def build_translation_input(
    rows: list[dict],
    lemmatizer: LatinBackoffLemmatizer,
    by_cltk: Dict[str, List[dict]],
    by_lemma: Dict[str, dict],
) -> list[dict]:
    """Compose the output rows: the input fields plus a ``glossary`` field."""
    payload: list[dict] = []
    lemma_cache: Dict[str, str] = {}

    for row in rows:
        glossary = build_glossary(row.get("source", ""), lemmatizer, by_cltk, by_lemma, lemma_cache)
        payload.append({**row, "glossary": glossary})

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Add glossary entries to a batch of sentences")
    parser.add_argument("filepath", type=str, help="Path to input JSON file")
    args = parser.parse_args()

    run_dir = Path(args.filepath).resolve().parent.parent / GLOSSARY_DIR_NAME
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {run_dir}")

    # Write the command line used to start the script
    cmd_line = ' '.join([shlex.quote(arg) for arg in sys.argv])
    command_file = run_dir / f"{SCRIPT_STEM}_command.txt"
    with open(command_file, "a", encoding="utf-8") as f:
        f.write(cmd_line + "\n")

    # Read the input JSON file and add glossary entries for each sentence
    with open(args.filepath, "r", encoding="utf-8") as f:
        batch_rows = json.load(f)

    if not isinstance(batch_rows, list):
        print(f"Input file '{args.filepath}' must contain a JSON array.", file=sys.stderr)
        sys.exit(1)

    by_cltk, by_lemma = load_lexicon(LEXICON_PATH)
    lemmatizer = LatinBackoffLemmatizer()
    payload = build_translation_input(batch_rows, lemmatizer, by_cltk, by_lemma)

    json_path = run_dir / Path(args.filepath).name
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    glossary_entries = sum(len(row.get("glossary", [])) for row in payload)
    print(f"Processed {len(batch_rows)} sentences")
    print(f"Glossary entries written: {glossary_entries}")
    print(f"JSON output written to: {json_path}")


if __name__ == "__main__":
    main()
