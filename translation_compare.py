#!/usr/bin/env python3
"""
Generate a Word translation-comparison document from two or more JSON files.

Expected JSON format (one object per sentence):
[
  {
    "id": "5203",
    "latin": "...",
    "dutch": "...",
    "reference": "Ia q. 75 pr.",
    "sequence": "5203"
  }
]

Usage examples:
    python translation_compare.py reference.json translation_a.json translation_b.json
    python translation_compare.py reference.json translation_a.json translation_b.json -o comparison.docx

The first JSON file is treated as the reference translation.
All files must contain the same sentence IDs. The `reference` field is used
to group sentences, and `sequence` is used for ordering within each group.

Dependencies:
    pip install python-docx
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_BREAK, WD_COLOR_INDEX
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.shared import Cm, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from difflib import SequenceMatcher


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class Config:
    ignore_case: bool = False
    ignore_punctuation: bool = True
    show_latin: bool = True
    highlight_reference: bool = False


# ---------------------------------------------------------------------------
# JSON loading / validation
# ---------------------------------------------------------------------------

def load_json(path: Path) -> List[dict]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: invalid JSON: {e}") from e

    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON array.")

    required = {"id", "dutch", "reference"}
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: item {i} is not an object.")
        missing = required - item.keys()
        if missing:
            raise ValueError(
                f"{path}: item {i} is missing required field(s): "
                + ", ".join(sorted(missing))
            )

    return data


def index_sentences(data: List[dict], path: Path) -> Dict[str, dict]:
    result = {}
    for item in data:
        key = str(item["id"])
        if key in result:
            raise ValueError(f"{path}: duplicate id {key!r}.")
        result[key] = item
    return result


def natural_sequence(value) -> Tuple:
    """Sort numeric-looking sequences numerically, otherwise naturally."""
    s = str(value)
    if re.fullmatch(r"\d+", s):
        return (0, int(s))
    parts = re.split(r"(\d+)", s)
    return tuple(int(p) if p.isdigit() else p.lower() for p in parts)


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def tokenize(text: str, ignore_punctuation: bool) -> List[str]:
    """
    Tokenize into words plus punctuation.

    With ignore_punctuation=True, punctuation is attached to no semantic token
    and excluded from the comparison. The original text is still represented
    as words for output.
    """
    if ignore_punctuation:
        # Unicode-aware-ish word tokenization: sequences of letters/numbers,
        # allowing internal apostrophes/hyphens.
        return re.findall(r"[\wÀ-ÖØ-öø-ÿĀ-ž]+(?:['’\-][\wÀ-ÖØ-öø-ÿĀ-ž]+)*", text, re.UNICODE)

    # Keep punctuation as separate tokens, while retaining whitespace-free
    # output reconstruction through the original token strings.
    return re.findall(
        r"[\wÀ-ÖØ-öø-ÿĀ-ž]+(?:['’\-][\wÀ-ÖØ-öø-ÿĀ-ž]+)*|[^\w\s]",
        text,
        re.UNICODE,
    )


def normalize_token(token: str, config: Config) -> str:
    return token.lower() if config.ignore_case else token


def diff_tokens(reference: str, candidate: str, config: Config):
    """
    Return a list of (operation, tokens) where operation is:
        equal, delete, insert, replace

    The reference is the baseline. Candidate insertions/replacements are
    highlighted. Reference deletions are represented in the candidate output
    as a deletion marker containing the missing reference text.
    """
    ref_tokens = tokenize(reference, config.ignore_punctuation)
    cand_tokens = tokenize(candidate, config.ignore_punctuation)

    ref_cmp = [normalize_token(x, config) for x in ref_tokens]
    cand_cmp = [normalize_token(x, config) for x in cand_tokens]

    matcher = SequenceMatcher(None, ref_cmp, cand_cmp, autojunk=False)

    operations = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            operations.append(("equal", cand_tokens[j1:j2]))
        elif tag == "delete":
            operations.append(("delete", ref_tokens[i1:i2]))
        elif tag == "insert":
            operations.append(("insert", cand_tokens[j1:j2]))
        elif tag == "replace":
            operations.append(("replace", {
                "reference": ref_tokens[i1:i2],
                "candidate": cand_tokens[j1:j2],
            }))

    return operations


# ---------------------------------------------------------------------------
# Word formatting helpers
# ---------------------------------------------------------------------------

def add_run_with_space(paragraph, text, *, bold=False, italic=False,
                       color=None, highlight=None, strike=False, size=None):
    run = paragraph.add_run(text)
    run.bold = bold
    run.italic = italic
    run.font.strike = strike
    if size:
        run.font.size = Pt(size)
    if color:
        run.font.color.rgb = RGBColor(*color)
    if highlight:
        run.font.highlight_color = highlight
    return run


def add_token_sequence(paragraph, tokens, *, bold=False, italic=False,
                       color=None, highlight=None, strike=False):
    """
    Add tokens with spaces between words. This intentionally produces clean,
    human-readable comparison text rather than preserving every original
    whitespace character.
    """
    for i, token in enumerate(tokens):
        if i:
            paragraph.add_run(" ")
        add_run_with_space(
            paragraph,
            token,
            bold=bold,
            italic=italic,
            color=color,
            highlight=highlight,
            strike=strike,
        )


def add_diff_to_paragraph(paragraph, operations):
    """
    Render candidate-side diff:
      equal    = normal
      insert   = yellow highlight
      replace  = yellow highlight on candidate
      delete   = red strikethrough '[− ...]' to show omitted reference words
    """
    first = True

    def separator():
        nonlocal first
        if not first:
            paragraph.add_run(" ")
        first = False

    for op, payload in operations:
        if op == "equal":
            for token in payload:
                separator()
                add_run_with_space(paragraph, token)

        elif op == "insert":
            for token in payload:
                separator()
                add_run_with_space(
                    paragraph,
                    token,
                    highlight=WD_COLOR_INDEX.YELLOW,
                )

        elif op == "replace":
            # Show candidate replacement in yellow.
            for token in payload["candidate"]:
                separator()
                add_run_with_space(
                    paragraph,
                    token,
                    highlight=WD_COLOR_INDEX.YELLOW,
                )

            # If candidate has no replacement text, show the deleted reference
            # text explicitly.
            if not payload["candidate"]:
                separator()
                add_run_with_space(
                    paragraph,
                    "[− " + " ".join(payload["reference"]) + "]",
                    color=(192, 0, 0),
                    strike=True,
                )

        elif op == "delete":
            separator()
            add_run_with_space(
                paragraph,
                "[− " + " ".join(payload) + "]",
                color=(192, 0, 0),
                strike=True,
            )


def count_differences(operations) -> int:
    count = 0
    for op, payload in operations:
        if op == "equal":
            continue
        if op in ("insert", "delete"):
            count += len(payload)
        elif op == "replace":
            count += max(
                len(payload["reference"]),
                len(payload["candidate"]),
            )
    return count


# ---------------------------------------------------------------------------
# DOCX styling
# ---------------------------------------------------------------------------

def set_cell_shading(cell, fill: str):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=100, start=120, bottom=100, end=120):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcMar = tcPr.first_child_found_in("w:tcMar")
    if tcMar is None:
        tcMar = OxmlElement("w:tcMar")
        tcPr.append(tcMar)

    for m, v in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tcMar.find(qn(f"w:{m}"))
        if node is None:
            node = OxmlElement(f"w:{m}")
            tcMar.append(node)
        node.set(qn("w:w"), str(v))
        node.set(qn("w:type"), "dxa")


def configure_document(doc: Document):
    section = doc.sections[0]
    section.top_margin = Cm(1.8)
    section.bottom_margin = Cm(1.8)
    section.left_margin = Cm(2.0)
    section.right_margin = Cm(2.0)

    styles = doc.styles

    normal = styles["Normal"]
    normal.font.name = "Aptos"
    normal.font.size = Pt(10.5)

    if "Comparison Translation" not in styles:
        style = styles.add_style("Comparison Translation", WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = normal
        style.font.name = "Aptos"
        style.font.size = Pt(10.5)
        style.paragraph_format.space_after = Pt(7)
        style.paragraph_format.line_spacing = 1.08

    if "Latin Source" not in styles:
        style = styles.add_style("Latin Source", WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = normal
        style.font.name = "Aptos"
        style.font.size = Pt(9.5)
        style.font.italic = True
        style.font.color.rgb = RGBColor(90, 90, 90)
        style.paragraph_format.space_after = Pt(7)

    if "Sentence ID" not in styles:
        style = styles.add_style("Sentence ID", WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = normal
        style.font.name = "Aptos"
        style.font.size = Pt(8)
        style.font.color.rgb = RGBColor(100, 100, 100)
        style.paragraph_format.space_after = Pt(2)


def add_label_paragraph(doc, label: str, text: str, *,
                        italic=False, color=None):
    p = doc.add_paragraph(style="Comparison Translation")
    r = p.add_run(label)
    r.bold = True
    if color:
        r.font.color.rgb = RGBColor(*color)
    r2 = p.add_run(text)
    r2.italic = italic
    return p


# ---------------------------------------------------------------------------
# Document generation
# ---------------------------------------------------------------------------

def generate_document(
    files: List[Path],
    output: Path,
    config: Config,
):
    if len(files) < 2:
        raise ValueError("Provide at least two JSON files.")

    datasets = [load_json(p) for p in files]
    indexes = [index_sentences(data, path) for data, path in zip(datasets, files)]

    reference_file = files[0]
    reference_index = indexes[0]

    # Require all files to contain the reference sentence IDs.
    ref_ids = set(reference_index)
    for path, index in zip(files[1:], indexes[1:]):
        missing = ref_ids - set(index)
        extra = set(index) - ref_ids

        if missing:
            print(
                f"Warning: {path.name} is missing {len(missing)} reference IDs.",
                file=sys.stderr,
            )
        if extra:
            print(
                f"Warning: {path.name} contains {len(extra)} extra IDs; "
                f"they will be ignored.",
                file=sys.stderr,
            )

    # Candidate names come from filenames, not from JSON content.
    candidate_names = [p.stem for p in files[1:]]

    # Sort by sequence, falling back to ID.
    sorted_items = sorted(
        reference_index.values(),
        key=lambda x: natural_sequence(x.get("sequence", x["id"])),
    )

    doc = Document()
    configure_document(doc)

    # Title page / heading.
    title = doc.add_heading("Translation Comparison", level=0)
    title.alignment = 1

    p = doc.add_paragraph()
    p.alignment = 1
    r = p.add_run(f"Reference translation: {reference_file.name}")
    r.bold = True

    p = doc.add_paragraph()
    p.alignment = 1
    p.add_run(
        "Differences in candidate translations are highlighted in yellow. "
        "Deleted reference words are shown in red strikethrough."
    )

    doc.add_paragraph("")

    # Overview.
    doc.add_heading("Translations", level=1)
    for i, name in enumerate(candidate_names, start=1):
        p = doc.add_paragraph()
        p.add_run(f"{i}. ").bold = True
        p.add_run(name)

    doc.add_page_break()

    current_reference = None
    group_count = 0
    sentence_count = 0
    diff_counts = {name: 0 for name in candidate_names}

    for ref_item in sorted_items:
        sentence_id = str(ref_item["id"])
        group = ref_item["reference"]

        if group != current_reference:
            if current_reference is not None:
                doc.add_page_break()

            current_reference = group
            group_count += 1
            doc.add_heading(group, level=1)
            if config.show_latin:
                p_latin = doc.add_paragraph(style="Latin Source")
            p_reference = doc.add_paragraph(style="Comparison Translation")
            r = p_reference.add_run("REFERENCE")
            r.bold = True
            r.font.color.rgb = RGBColor(31, 78, 121)
            p_reference.add_run("\n")
            p_candidate = {}
            for name in candidate_names:
                p_candidate[name] = doc.add_paragraph(style="Comparison Translation")
                r = p_candidate[name].add_run(name.upper())
                r.bold = True
                r.font.color.rgb = RGBColor(70, 70, 70)
                p_candidate[name].add_run("\n")

        sentence_count += 1

        if config.show_latin and ref_item.get("latin"):
            p_latin.add_run(ref_item["latin"])

        # Reference translation.
        p_reference.add_run(str(ref_item["dutch"]))
        p_reference.add_run(" ")

        # Candidate translations.
        for idx, (name, index) in enumerate(zip(candidate_names, indexes[1:])):
            candidate_item = index.get(sentence_id)
            if candidate_item is None:
                r = p_candidate[name].add_run("[MISSING SENTENCE]")
                r.bold = True
                r.font.color.rgb = RGBColor(192, 0, 0)
                continue

            operations = diff_tokens(
                str(ref_item["dutch"]),
                str(candidate_item["dutch"]),
                config,
            )
            diff_counts[name] += count_differences(operations)
            add_diff_to_paragraph(p_candidate[name], operations)
            p_candidate[name].add_run(" ")

    # Summary at end.
    doc.add_page_break()
    doc.add_heading("Comparison Summary", level=1)

    p = doc.add_paragraph()
    p.add_run("Sentence groups: ").bold = True
    p.add_run(str(group_count))
    p.add_run("    ")
    p.add_run("Sentences: ").bold = True
    p.add_run(str(sentence_count))

    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"

    headers = ["Translation", "Difference tokens", "Approx. rate"]
    for cell, text in zip(table.rows[0].cells, headers):
        cell.text = text
        set_cell_shading(cell, "D9EAF7")
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        set_cell_margins(cell)

    # Use total reference token count as a denominator.
    total_ref_tokens = sum(
        len(tokenize(str(item["dutch"]), config.ignore_punctuation))
        for item in sorted_items
    )

    for name in candidate_names:
        row = table.add_row().cells
        diff = diff_counts[name]
        rate = (100.0 * diff / total_ref_tokens) if total_ref_tokens else 0.0

        values = [name, str(diff), f"{rate:.1f}%"]
        for cell, text in zip(row, values):
            cell.text = text
            set_cell_margins(cell)

    # Footer.
    for section in doc.sections:
        footer = section.footer
        p = footer.paragraphs[0]
        p.alignment = 2
        p.add_run("Translation comparison")

    doc.save(output)

    print(f"Created: {output}")
    print(f"Sentence groups: {group_count}")
    print(f"Sentences:       {sentence_count}")
    for name in candidate_names:
        print(f"{name}: {diff_counts[name]} differing tokens")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate a Word document comparing Dutch translations."
    )
    parser.add_argument(
        "json_files",
        nargs="+",
        type=Path,
        help=(
            "Two or more JSON files. The FIRST file is the reference "
            "translation; the remaining files are candidate translations."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("translation_comparison.docx"),
        help="Output DOCX filename (default: translation_comparison.docx).",
    )
    parser.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Treat capitalization differences as differences.",
    )
    parser.add_argument(
        "--keep-punctuation",
        action="store_true",
        help="Include punctuation in the comparison.",
    )
    parser.add_argument(
        "--no-latin",
        action="store_true",
        help="Do not include the Latin source.",
    )
    parser.add_argument(
        "--no-sentence-ids",
        action="store_true",
        help="Do not display sentence IDs.",
    )

    args = parser.parse_args()

    if len(args.json_files) < 2:
        parser.error("At least two JSON files are required.")

    config = Config(
        ignore_case=not args.case_sensitive,
        ignore_punctuation=not args.keep_punctuation,
        show_latin=not args.no_latin,
    )

    try:
        generate_document(args.json_files, args.output, config)
    except (ValueError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
