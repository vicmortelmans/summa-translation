#!/usr/bin/env python3
"""Build Aquinas summary paragraphs from translated JSON data."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

BOOK_MAP = {
    "ia": 1,
    "ia-iiae": 2,
    "iia-iiae": 3,
    "iiia": 4,
    "suppl": 5,
}

VALID_LEMMAS = {"pr", "arg", "sc", "co", "ad"}


def load_records(path: Path) -> list[dict[str, Any]]:
    """Load a JSON list or a dict containing a list."""
    raw = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(raw, list):
        records = raw
    elif isinstance(raw, dict):
        for key in ("items", "records", "translations", "data", "results"):
            value = raw.get(key)
            if isinstance(value, list):
                records = value
                break
        else:
            raise ValueError(f"No list-like records found in JSON file: {path}")
    else:
        raise TypeError(f"Unsupported JSON root type in {path}: {type(raw).__name__}")

    cleaned: list[dict[str, Any]] = []
    for item in records:
        if isinstance(item, dict):
            cleaned.append(item)
    return cleaned


def normalize_reference_text(reference: str) -> str:
    text = reference.strip()
    text = text.replace("s. c.", "sc.")
    text = text.replace("s.c.", "sc.")
    text = text.replace("S. C.", "sc.")
    text = re.sub(r"\s+", " ", text)
    return text


def parse_reference(reference: str) -> tuple[int, int, int | None, str] | None:
    text = normalize_reference_text(reference)
    match = re.match(
        r"^(Ia|Ia-IIae|IIa-IIae|IIIa|Suppl)\s+q\.\s*(\d+)(?:\s+a\.\s*(\d+))?\s*(pr|arg|sc|co|ad)\.?\s*(?:\d+)?\s*$",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    book_token, quaestio_text, article_text, lemma = match.groups()
    book = BOOK_MAP.get(book_token.lower())
    if book is None:
        return None

    lemma = lemma.lower()
    if lemma == "sc":
        lemma = "sc"
    elif lemma not in VALID_LEMMAS:
        return None

    article = int(article_text) if article_text is not None else None
    return book, int(quaestio_text), article, lemma


def build_paragraph_id(reference: str) -> str:
    parsed = parse_reference(reference)
    if parsed is None:
        raise ValueError(f"Unable to parse reference: {reference!r}")

    book, quaestio, article, lemma = parsed
    if article is None:
        return f"{book}.{quaestio}.{lemma}"
    return f"{book}.{quaestio}.{article}.{lemma}"


def collect_paragraphs(records: list[dict[str, Any]]) -> list[tuple[str, str]]:
    groups: dict[str, list[str]] = {}
    order: list[str] = []

    for record in records:
        reference = str(record.get("reference", "")).strip()
        dutch = record.get("dutch")
        if not reference or dutch is None:
            continue

        normalized_reference = normalize_reference_text(reference)
        if parse_reference(normalized_reference) is None:
            continue

        if normalized_reference not in groups:
            order.append(normalized_reference)
        groups.setdefault(normalized_reference, []).append(" ".join(str(dutch).split()))

    paragraphs: list[tuple[str, str]] = []
    for reference in order:
        sentences = groups[reference]
        paragraph = " ".join(sentence for sentence in sentences if sentence)
        if paragraph:
            paragraphs.append((build_paragraph_id(reference), paragraph))

    return paragraphs


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Summa paragraph text file from JSON translation entries.")
    parser.add_argument("json_path", type=Path, help="Path to the input JSON file with dutch/reference entries")
    args = parser.parse_args()

    input_path = args.json_path.resolve()
    records = load_records(input_path)
    paragraphs = collect_paragraphs(records)

    output_path = Path("Aquino_Summa_00.txt")
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        for paragraph_id, paragraph in paragraphs:
            handle.write(f"{paragraph_id}\n")
            handle.write(f"{paragraph}\n")

    print(f"Wrote {len(paragraphs)} paragraphs to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
