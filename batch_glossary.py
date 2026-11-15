#!/usr/bin/env python3
"""Add glossary entries to a batch of sentences.

The script reads a batch JSON file (a list of objects that each contain a
``source`` sentence), lemmatizes every word of the sentence with CLTK's
``LatinBackoffLemmatizer`` and looks the lemma up in the Axters scholastic
lexicon.  Matches are added to the input objects under a ``glossary`` field:
a list of objects with the word as it appears in the sentence (``source``),
its lemmatized form (``lemma``) and the Dutch definition from the lexicon
(``translation``).

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


def _add_to_index(index: Dict[str, dict], key: str, entry: dict) -> None:
    """Add an entry to an index, preferring entries whose lemma equals the key.

    Several lexicon entries can share the same CLTK lemma (e.g. ``ago`` is the
    CLTK lemma of ``actus``, ``agens`` and ``agere``).  In that case the entry
    whose own lemma matches the key (``agere`` for the form ``agere``) is the
    most useful one, so it takes precedence.
    """
    if not key:
        return

    current = index.get(key)
    if current is None:
        index[key] = entry
        return

    if normalize_key(entry.get("lemma", "")) == key and normalize_key(current.get("lemma", "")) != key:
        index[key] = entry


def load_lexicon(path: Path) -> tuple[Dict[str, dict], Dict[str, dict]]:
    """Load the Axters lexicon and return indexes by CLTK lemma and by lemma."""
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

    by_cltk: Dict[str, dict] = {}
    by_lemma: Dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict) or "definition" not in entry:
            continue
        _add_to_index(by_cltk, normalize_key(entry.get("cltk", "")), entry)
        _add_to_index(by_lemma, normalize_key(entry.get("lemma", "")), entry)

    return by_cltk, by_lemma


# ============================================================================
# Glossary
# ============================================================================


def find_glossary_entry(token: str, lemma: str, by_cltk: Dict[str, dict], by_lemma: Dict[str, dict]) -> dict | None:
    """Return the lexicon entry for a token and its lemma, if any."""
    for key in (normalize_key(lemma), normalize_key(token)):
        if not key:
            continue
        entry = by_lemma.get(key) or by_cltk.get(key)
        if entry is not None:
            return entry
    return None


def build_glossary(
    sentence: str,
    lemmatizer: LatinBackoffLemmatizer,
    by_cltk: Dict[str, dict],
    by_lemma: Dict[str, dict],
    lemma_cache: Dict[str, str],
) -> List[dict]:
    """Return the glossary entries found in a single sentence.

    Each entry contains the word as it appears in the sentence (``source``),
    its lemmatized form (``lemma``) and the Dutch definition from the lexicon
    (``translation``).  Duplicate lemmas are dropped, preserving the order in
    which the words first appear in the sentence.
    """
    tokens = WORD_PATTERN.findall(sentence)
    if not tokens:
        return []

    lowered = [token.lower() for token in tokens]
    unknown = [token for token in lowered if token not in lemma_cache]
    if unknown:
        for token, lemma in lemmatizer.lemmatize(unknown):
            lemma_cache[token] = lemma

    glossary: List[dict] = []
    seen: set[str] = set()
    for source, token in zip(tokens, lowered):
        lemma = lemma_cache.get(token, token)
        entry = find_glossary_entry(token, lemma, by_cltk, by_lemma)
        if entry is None:
            continue

        lemma_key = normalize_key(lemma)
        if not lemma_key or lemma_key in seen:
            continue

        seen.add(lemma_key)
        glossary.append(
            {
                "source": source,
                "lemma": lemma,
                "translation": entry["definition"],
            }
        )

    return glossary


def build_translation_input(
    rows: list[dict],
    lemmatizer: LatinBackoffLemmatizer,
    by_cltk: Dict[str, dict],
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
