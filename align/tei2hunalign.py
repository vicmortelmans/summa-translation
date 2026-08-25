#!/usr/bin/env python3

import argparse
import re
import xml.etree.ElementTree as ET


def local_name(tag):
    """Return the local part of an XML tag, ignoring namespaces."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def normalize(text):
    """Normalize whitespace."""
    if text is None:
        return ""

    text = re.sub(r"\s+", " ", text)
    return text.strip()


def child_elements(element, name):
    """Return direct children with the given local name."""
    return [
        child
        for child in element
        if local_name(child.tag) == name
    ]


def descendants(element, name):
    """Return all descendants with the given local name."""
    return [
        child
        for child in element.iter()
        if local_name(child.tag) == name
    ]


def text_content(element):
    """Get all text contained in an XML element."""
    return normalize("".join(element.itertext()))


def extract_entries(filename):

    tree = ET.parse(filename)
    root = tree.getroot()

    pairs = set()

    # Find every <entry>, regardless of XML namespace.
    entries = [
        element
        for element in root.iter()
        if local_name(element.tag) == "entry"
    ]

    print(f"Found {len(entries):,} dictionary entries")

    for entry in entries:

        # Find the headword.
        forms = child_elements(entry, "form")

        headwords = []

        for form in forms:
            for orth in child_elements(form, "orth"):
                word = text_content(orth)
                if word:
                    headwords.append(word)

        if not headwords:
            continue

        # Find translations.
        translations = []

        for cit in descendants(entry, "cit"):

            # Only translation citations.
            if cit.get("type") not in ("trans", "translation"):
                continue

            for quote in child_elements(cit, "quote"):
                translation = text_content(quote)

                if translation:
                    translations.append(translation)

        if not translations:
            continue

        # Create all headword/translation combinations.
        for dutch in headwords:
            for latin in translations:
                dutch = normalize(dutch)
                latin = normalize(latin)

                if dutch and latin:
                    pairs.add((dutch, latin))

    return sorted(pairs)


def main():

    parser = argparse.ArgumentParser(
        description="Convert a FreeDict TEI dictionary to Hunalign format."
    )

    parser.add_argument(
        "input",
        help="FreeDict TEI XML file"
    )

    parser.add_argument(
        "output",
        help="Hunalign dictionary output file"
    )

    args = parser.parse_args()

    pairs = extract_entries(args.input)

    with open(args.output, "w", encoding="utf-8") as f:
        for dutch, latin in pairs:
            f.write(f"{dutch} @ {latin}\n")

    print(f"Wrote {len(pairs):,} dictionary entries to {args.output}")


if __name__ == "__main__":
    main()