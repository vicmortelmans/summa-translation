from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET


def normalize_text(node) -> str:
    parts = []
    for part in node.itertext():
        text = " ".join(part.split())
        if text:
            parts.append(text)
    return " ".join(parts)


def contains_abest(node) -> bool:
    return any(child.tag == "abest" for child in node.iter())


def find_input_file(base_dir: Path) -> Path:
    candidates = [
        base_dir / "xml_latin_nl.xml",
        base_dir / "xml_latin_nl_xml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not find the XML input file. Expected one of: "
        + ", ".join(str(path.name) for path in candidates)
    )


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    input_path = find_input_file(base_dir)

    tree = ET.parse(input_path)
    root = tree.getroot()

    latin_parts: list[str] = []
    nl_parts: list[str] = []

    for node in root.iter():
        if node.tag == "latin":
            text = normalize_text(node)
            if text:
                latin_parts.append(text)
        elif node.tag == "nl":
            if contains_abest(node):
                continue
            text = normalize_text(node)
            if text:
                nl_parts.append(text)

    (base_dir / "latin_plain.txt").write_text("\n\n".join(latin_parts), encoding="utf-8")
    (base_dir / "nl_plain.txt").write_text("\n\n".join(nl_parts), encoding="utf-8")

    print(f"Wrote {len(latin_parts)} Latin entries to {base_dir / 'latin_plain.txt'}")
    print(f"Wrote {len(nl_parts)} Dutch entries to {base_dir / 'nl_plain.txt'}")


if __name__ == "__main__":
    main()
