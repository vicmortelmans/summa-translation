#!/usr/bin/env python3

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def ensure_runtime() -> None:
    try:
        import nltk  # noqa: F401
    except ModuleNotFoundError:
        venv_python = Path(__file__).resolve().parent / "env" / "bin" / "python"
        if venv_python.exists():
            os.execv(str(venv_python), [str(venv_python), str(__file__)] + sys.argv[1:])
        raise


ensure_runtime()

import nltk
from nltk.tokenize import PunktSentenceTokenizer


def ensure_punkt_data() -> None:
    try:
        nltk.data.find("tokenizers/punkt")
    except LookupError:
        nltk.download("punkt")


def normalize_text(node) -> str:
    parts: list[str] = []
    for text in node.itertext():
        cleaned = " ".join(text.split())
        if cleaned:
            parts.append(cleaned)
    return " ".join(parts)


def has_abest(node) -> bool:
    return any(child.tag == "abest" for child in node.iter())


def load_training_tokenizer(corpus_path: Path) -> PunktSentenceTokenizer:
    training_text = corpus_path.read_text(encoding="utf-8")
    return PunktSentenceTokenizer(training_text)


def iter_valid_lemma_pairs(root: ET.Element):
    for lemma in root.iter("lemma"):
        latin_node = None
        nl_node = None
        for child in lemma:
            if child.tag == "latin":
                latin_node = child
            elif child.tag == "nl":
                nl_node = child

        if latin_node is None or nl_node is None:
            continue
        if has_abest(nl_node):
            continue

        yield normalize_text(latin_node), normalize_text(nl_node)


def strip_periods_in_bracketed_references(text):
    def remove_periods(match):
        return match.group(0).replace(".", "")

    return re.sub(r"\([^)]*\)|\[[^\]]*\]", remove_periods, text)


def write_stem_file(output_path: Path, entries: list[str], tokenizer: PunktSentenceTokenizer) -> None:
    lines: list[str] = []
    for entry in entries:
        # Remove periods inside bracketed references so they do not confuse sentence detection.
        entry = strip_periods_in_bracketed_references(entry)

        sentences = tokenizer.tokenize(entry)
        for sentence in sentences:
            # Separate common punctuation from words.
            sentence = re.sub(r'([.,!?;:()\[\]{}])', r' \1 ', sentence)

            # Normalize whitespace.
            sentence = re.sub(r'\s+', ' ', sentence).strip()

            if sentence:
                lines.append(sentence)
        lines.append("<p>")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ensure_punkt_data()

    base_dir = Path(__file__).resolve().parent
    xml_path = base_dir / "xml_latin_nl.xml"
    if not xml_path.exists():
        raise FileNotFoundError(f"XML file not found: {xml_path}")

    tree = ET.parse(xml_path)
    root = tree.getroot()

    latin_entries: list[str] = []
    nl_entries: list[str] = []
    for latin_text, nl_text in iter_valid_lemma_pairs(root):
        latin_entries.append(latin_text)
        nl_entries.append(nl_text)

    latin_tokenizer = load_training_tokenizer(base_dir / "latin_plain.txt")
    nl_tokenizer = load_training_tokenizer(base_dir / "nl_plain.txt")

    write_stem_file(base_dir / "latin.stem", latin_entries, latin_tokenizer)
    write_stem_file(base_dir / "nl.stem", nl_entries, nl_tokenizer)

    print(f"Wrote {len(latin_entries)} Latin stem entries to {base_dir / 'latin.stem'}")
    print(f"Wrote {len(nl_entries)} Dutch stem entries to {base_dir / 'nl.stem'}")


if __name__ == "__main__":
    main()
