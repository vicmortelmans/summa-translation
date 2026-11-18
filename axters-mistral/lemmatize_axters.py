#!/usr/bin/env python3
"""
Add CLTK lemmatizations to the Axters scholastic lexicon.

This script reads ``axters-scholastiek-lexicon-1937.json``, lemmatizes each
``lemma`` value with CLTK's ``LatinBackoffLemmatizer``, and writes the result
to ``axters-scholastiek-lexicon-1937-cltk.json``.  Every entry keeps its
original ``lemma`` and ``definition`` fields and gains an extra ``cltk``
field containing the lemmatized form.

Usage:
    ./env/bin/python axters-mistral/lemmatize_axters.py
"""

import json
import re
from pathlib import Path
from typing import Dict, List

from cltk.lemmatize.lat import LatinBackoffLemmatizer


INPUT_FILE = "axters-scholastiek-lexicon-1937.json"
OUTPUT_FILE = "axters-scholastiek-lexicon-1937-cltk.json"

# Latin words may contain letters only; parentheses and other punctuation
# (e.g. in "abstinere (se)") are not part of the lemma itself.
WORD_PATTERN = re.compile(r"[A-Za-z]+")

# Morpheus/Perseus lemma IDs append a homonym number (e.g. "appeto1",
# "lego2").  Strip it so the ``cltk`` field contains a plain lemma.
HOMONYM_SUFFIX_PATTERN = re.compile(r"\d+$")


def lemmatize_lemma(lemmatizer: LatinBackoffLemmatizer, lemma: str) -> str:
    """Return the CLTK lemmatization of a lexicon lemma.

    Multi-word lemmas are tokenized into words, and the lemmas of those
    tokens are joined with single spaces.  Punctuation is ignored.
    """
    tokens = WORD_PATTERN.findall(lemma)
    if not tokens:
        return ""

    # ``lemmatize`` returns (token, lemma) tuples.  Punctuation tokens are
    # returned with the lemma "punc", but they are already filtered out by
    # WORD_PATTERN.
    lemmas = [
        HOMONYM_SUFFIX_PATTERN.sub("", cltk_lemma)
        for _token, cltk_lemma in lemmatizer.lemmatize(tokens)
        if cltk_lemma != "punc"
    ]
    return " ".join(lemmas)


def main() -> None:
    """Lemmatize every entry and write the enriched JSON file."""
    base_dir = Path(__file__).resolve().parent
    input_path = base_dir / INPUT_FILE
    output_path = base_dir / OUTPUT_FILE

    with input_path.open("r", encoding="utf-8") as input_file:
        entries = json.load(input_file)

    lemmatizer = LatinBackoffLemmatizer()
    cache: Dict[str, str] = {}
    output_entries: List[dict] = []

    for entry in entries:
        lemma = entry["lemma"]
        if lemma not in cache:
            cache[lemma] = lemmatize_lemma(lemmatizer, lemma)

        output_entries.append(
            {
                "lemma": lemma,
                "cltk": cache[lemma],
                "definition": entry["definition"],
            }
        )

    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(output_entries, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")

    print(f"Lemmatized {len(output_entries)} entries -> {output_path}")


if __name__ == "__main__":
    main()
