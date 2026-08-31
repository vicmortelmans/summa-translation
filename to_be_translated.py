#!/usr/bin/env python3
"""Export untranslated Latin sentences from the XML corpus to a TSV file."""

from __future__ import annotations

import csv
from pathlib import Path
import xml.etree.ElementTree as ET

import nltk
from nltk.tokenize import PunktSentenceTokenizer


XML_PATH = Path(__file__).with_name("xml_latin_nl.xml")
OUTPUT_PATH = Path(__file__).with_name("to_be_translated.tsv")


def ensure_punkt() -> None:
    """Download NLTK sentence tokenizer data if it is not available."""
    try:
        PunktSentenceTokenizer()
    except LookupError:
        nltk.download("punkt")


def text_of(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def contains_abest(node: ET.Element | None) -> bool:
    if node is None:
        return False
    return any(child.tag == "abest" for child in node.iter())


def iter_untranslated_lemmas(root: ET.Element):
    for lemma in root.iter("lemma"):
        nl = lemma.find("nl")
        if contains_abest(nl):
            yield lemma


def main() -> None:
    ensure_punkt()
    tokenizer = PunktSentenceTokenizer()

    tree = ET.parse(XML_PATH)
    root = tree.getroot()

    rows: list[dict[str, object]] = []
    icx = 0

    for lemma in iter_untranslated_lemmas(root):
        reference_element = lemma.find("reference")
        if reference_element is None:
            reference_element = lemma.find("refernce")
        if reference_element is None:
            reference_element = ET.Element("reference")

        latin = lemma.find("latin")
        if latin is None:
            continue

        latin_text = text_of(latin)
        if not latin_text:
            continue

        sentences = tokenizer.tokenize(latin_text)
        merged_sentences: list[str] = []

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            if merged_sentences and sentence and sentence[0].isalpha() and sentence[0].islower():
                merged_sentences[-1] = f"{merged_sentences[-1]} {sentence}"
            else:
                merged_sentences.append(sentence)

        for sentence in merged_sentences:
            icx += 1
            rows.append(
                {
                    "latin": sentence,
                    "reference": text_of(reference_element),
                    "icx": icx,
                }
            )

    with OUTPUT_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["latin", "reference", "icx"],
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} sentence rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
