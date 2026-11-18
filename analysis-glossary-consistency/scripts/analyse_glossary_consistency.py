#!/usr/bin/env python3
"""Find lemmas translated consistently within batches but differently across batches.

This is a triage tool, not an automatic correction step.  It reads
``glossary-application-inventory.csv`` (written by
``glossary_application_inventory.py``) for a run and looks for lemmas whose
Dutch rendering is stable inside each batch, but whose dominant rendering
changes between batches.  Such lemmas are candidates for human review and,
eventually, a cross-batch harmonisation pass.

Method
------
For every glossary entry the inventory gives the chosen glossary alternative
(``definition_index``) or 0 when the translator used another rendering.  A
rendering of an occurrence is therefore one of:

* the text of the chosen glossary alternative, or
* ``__other__`` for everything not covered by the glossary.

A batch is *informative* for a lemma when it contains at least
``--min-batch-count`` occurrences: one or two occurrences are too little
evidence.  For each informative batch the dominant rendering and its purity
(dominant count / batch count) are computed.

A lemma becomes a candidate when all of the following hold:

* the glossary offers at least ``--min-alternatives`` renderings
  (default 2: the situation described for *beatitudo*);
* at least ``--min-batches`` informative batches exist;
* every informative batch has purity >= ``--min-purity`` (the "consistent
  within a batch" condition);
* the informative batches contain at least ``--min-total`` occurrences;
* at least ``--min-switch-count`` occurrences lie in batches whose dominant
  rendering differs from the global dominant rendering (the "inconsistent
  across batches" condition).

Candidates are ranked by the absolute number of cross-batch switched
occurrences, then by their share of the qualifying occurrences.  The absolute
count is the primary key because a switch in a lemma with hundreds of
occurrences matters more than the same proportion in a rare lemma.

Outputs (default ``<run-dir>/glossary-consistency/``)
-----------------------------------------------------
* ``candidate_lemmas.csv`` — ranked triage list with all metrics.
* ``inter-batch-lemmata-disambiguation.csv`` — one row per candidate lemma
  with its dominant translation over the whole run (every occurrence, not only
  the informative batches; blank when the dominant rendering is ``__other__``),
  ready to be filled in for a follow-up pass.
* ``lemma_batch_counts.csv`` — long table: lemma x batch x rendering counts.
* ``lemma_occurrences.csv`` — every occurrence of the candidate lemmas,
  including sentence id, batch and translation, for manual inspection.
* ``report.html`` — human-readable report with a stacked bar chart and a
  corpus timeline per candidate lemma.
* ``summary.json`` — thresholds, totals and the candidate list.

Usage::

    ./env/bin/python analysis-glossary-consistency/scripts/analyse_glossary_consistency.py run-glossary

The script is designed to scale to larger production runs: only candidate
lemmas are rendered in the HTML report (``--max-report-lemmas``), while the
CSV files keep the complete data.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

INVENTORY_NAME = "glossary-application-inventory.csv"
OUTPUT_DIR_NAME = "glossary-consistency"
OTHER = "__other__"
OTHER_LABEL = "other (not in glossary)"
PALETTE = (
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948",
    "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Occurrence:
    """One glossary application in one sentence."""

    sentence_id: int
    batch: int
    source: str
    lemma: str
    glossary_translation: str
    alternatives: tuple[str, ...]
    definition_index: int
    dutch_word: str
    sentence_translation: str

    @property
    def variant(self) -> str:
        if 1 <= self.definition_index <= len(self.alternatives):
            return self.alternatives[self.definition_index - 1]
        return OTHER

    @property
    def variant_index(self) -> int:
        if 1 <= self.definition_index <= len(self.alternatives):
            return self.definition_index
        return 0


@dataclass
class BatchStats:
    """Aggregated occurrences of one lemma in one batch."""

    batch: int
    occurrences: list[Occurrence]
    counts: Counter[str]
    informative: bool
    dominant: str
    dominant_count: int
    purity: float

    @property
    def total(self) -> int:
        return len(self.occurrences)

    @property
    def sentence_id_min(self) -> int:
        return min(occ.sentence_id for occ in self.occurrences)

    @property
    def sentence_id_max(self) -> int:
        return max(occ.sentence_id for occ in self.occurrences)


@dataclass
class LemmaAnalysis:
    """Metrics for one (lemma, glossary translation) group."""

    lemma: str
    glossary_translation: str
    alternatives: tuple[str, ...]
    occurrences: list[Occurrence]
    batches: list[BatchStats]
    informative_batches: list[BatchStats]
    global_counts: Counter[str]
    global_dominant: str
    switch_batches: list[BatchStats]
    switch_occurrences: int
    total_qualifying: int
    within_batch_purity: float
    global_purity: float
    min_batch_purity: float

    @property
    def alternatives_count(self) -> int:
        return len(self.alternatives)

    @property
    def switch_share(self) -> float:
        return self.switch_occurrences / self.total_qualifying if self.total_qualifying else 0.0

    @property
    def affected_batches(self) -> int:
        return len(self.switch_batches)

    @property
    def distinct_variants(self) -> int:
        return len(self.global_counts)

    @property
    def distinct_dominants(self) -> int:
        return len({batch.dominant for batch in self.informative_batches})


def parse_alternatives(text: str) -> tuple[str, ...]:
    """Split a glossary translation into its comma-separated alternatives."""
    return tuple(part.strip() for part in text.split(",") if part.strip())


def variant_order(alternatives: Sequence[str]) -> dict[str, int]:
    """Return a deterministic tie-break order: glossary order, then other."""
    order = {alternative: index for index, alternative in enumerate(alternatives)}
    order[OTHER] = len(alternatives)
    return order


def dominant_variant(counts: Counter[str], order: dict[str, int]) -> str:
    """Return the most frequent variant, breaking ties by glossary order."""
    if not counts:
        return OTHER
    return min(
        counts,
        key=lambda variant: (-counts[variant], order.get(variant, len(order)), variant),
    )


def load_inventory(path: Path, translations: dict[int, str] | None = None) -> list[Occurrence]:
    """Read the glossary application inventory into occurrence records."""
    translations = translations or {}
    occurrences: list[Occurrence] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "sentence_id", "batch", "source", "lemma", "translation",
            "alternatives", "dutch_word", "definition_index",
        }
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        for row in reader:
            try:
                sentence_id = int(row["sentence_id"])
                batch = int(row["batch"])
                definition_index = int(row["definition_index"])
            except ValueError as exc:
                raise ValueError(f"{path}: invalid numeric field in row {row}") from exc
            occurrences.append(Occurrence(
                sentence_id=sentence_id,
                batch=batch,
                source=row["source"],
                lemma=row["lemma"],
                glossary_translation=row["translation"],
                alternatives=parse_alternatives(row["translation"]),
                definition_index=definition_index,
                dutch_word=row["dutch_word"],
                sentence_translation=translations.get(sentence_id, ""),
            ))
    if not occurrences:
        raise ValueError(f"{path}: no data rows")
    return occurrences


def group_occurrences(
    occurrences: Iterable[Occurrence],
) -> dict[tuple[str, str], list[Occurrence]]:
    """Group occurrences by lemma and exact glossary definition list."""
    groups: dict[tuple[str, str], list[Occurrence]] = defaultdict(list)
    for occurrence in occurrences:
        groups[(occurrence.lemma, occurrence.glossary_translation)].append(occurrence)
    return groups


def analyse_group(
    occurrences: list[Occurrence],
    order: dict[str, int],
    min_batch_count: int,
    min_batches: int,
    min_total: int,
    min_purity: float,
    min_switch_count: int,
    min_alternatives: int,
) -> LemmaAnalysis | None:
    """Compute batch statistics and return a candidate analysis, if any."""
    alternatives = occurrences[0].alternatives
    if len(alternatives) < min_alternatives:
        return None

    by_batch: dict[int, list[Occurrence]] = defaultdict(list)
    for occurrence in occurrences:
        by_batch[occurrence.batch].append(occurrence)

    batches: list[BatchStats] = []
    for batch_number in sorted(by_batch):
        batch_occurrences = sorted(by_batch[batch_number], key=lambda occ: occ.sentence_id)
        counts = Counter(occurrence.variant for occurrence in batch_occurrences)
        dominant = dominant_variant(counts, order)
        dominant_count = counts[dominant]
        purity = dominant_count / len(batch_occurrences)
        batches.append(BatchStats(
            batch=batch_number,
            occurrences=batch_occurrences,
            counts=counts,
            informative=len(batch_occurrences) >= min_batch_count,
            dominant=dominant,
            dominant_count=dominant_count,
            purity=purity,
        ))

    informative = [batch for batch in batches if batch.informative]
    if len(informative) < min_batches:
        return None
    if any(batch.purity < min_purity for batch in informative):
        return None

    global_counts: Counter[str] = Counter()
    for batch in informative:
        global_counts.update(batch.counts)
    global_dominant = dominant_variant(global_counts, order)

    switch_batches = [
        batch for batch in informative if batch.dominant != global_dominant
    ]
    switch_occurrences = sum(batch.total for batch in switch_batches)
    total_qualifying = sum(batch.total for batch in informative)

    if total_qualifying < min_total:
        return None
    if switch_occurrences < min_switch_count:
        return None
    if len({batch.dominant for batch in informative}) < 2:
        return None

    return LemmaAnalysis(
        lemma=occurrences[0].lemma,
        glossary_translation=occurrences[0].glossary_translation,
        alternatives=alternatives,
        occurrences=sorted(occurrences, key=lambda occ: occ.sentence_id),
        batches=batches,
        informative_batches=informative,
        global_counts=global_counts,
        global_dominant=global_dominant,
        switch_batches=switch_batches,
        switch_occurrences=switch_occurrences,
        total_qualifying=total_qualifying,
        within_batch_purity=sum(batch.dominant_count for batch in informative) / total_qualifying,
        global_purity=global_counts[global_dominant] / total_qualifying,
        min_batch_purity=min(batch.purity for batch in informative),
    )


def load_sentence_translations(run_dir: Path, translation_dir: Path | None = None) -> dict[int, str]:
    """Load sentence translations from ``30_translation/*.json``."""
    directory = translation_dir or (run_dir / "30_translation")
    translations: dict[int, str] = {}
    for path in sorted(directory.glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        for item in payload.get("translations", []):
            sentence_id = int(item["id"])
            if sentence_id in translations:
                raise ValueError(f"duplicate translation id {sentence_id} in {path}")
            translations[sentence_id] = item["translation"]
    if not translations:
        raise ValueError(f"no translations found in {directory}")
    return translations


def analyse_run(run_dir: Path, args: argparse.Namespace) -> tuple[list[LemmaAnalysis], dict]:
    """Analyse all lemma groups and return candidates plus run metadata."""
    inventory_path = Path(args.inventory) if args.inventory else run_dir / INVENTORY_NAME
    translation_dir = (Path(args.translation_dir) if args.translation_dir
                       else run_dir / "30_translation")
    translations = load_sentence_translations(run_dir, translation_dir)
    occurrences = load_inventory(inventory_path, translations)
    groups = group_occurrences(occurrences)

    candidates: list[LemmaAnalysis] = []
    for group_occurrences_list in groups.values():
        alternatives = group_occurrences_list[0].alternatives
        analysis = analyse_group(
            group_occurrences_list,
            variant_order(alternatives),
            min_batch_count=args.min_batch_count,
            min_batches=args.min_batches,
            min_total=args.min_total,
            min_purity=args.min_purity,
            min_switch_count=args.min_switch_count,
            min_alternatives=args.min_alternatives,
        )
        if analysis is not None:
            candidates.append(analysis)

    candidates.sort(key=lambda item: (
        -item.switch_occurrences,
        -item.switch_share,
        item.lemma,
        item.glossary_translation,
    ))

    metadata = {
        "run_dir": str(run_dir),
        "inventory": str(inventory_path),
        "sentences_total": len(translations),
        "sentences_with_glossary": len({occ.sentence_id for occ in occurrences}),
        "occurrences": len(occurrences),
        "lemma_groups": len(groups),
        "lemmas": len({occ.lemma for occ in occurrences}),
        "sentence_id_min": min(occ.sentence_id for occ in occurrences),
        "sentence_id_max": max(occ.sentence_id for occ in occurrences),
        "batch_count": len({occ.batch for occ in occurrences}),
        "candidate_count": len(candidates),
    }
    return candidates, metadata


def _format_float(value: float) -> str:
    return f"{value:.4f}"


def candidate_rows(candidates: Sequence[LemmaAnalysis]) -> list[list[str]]:
    """Build rows for ``candidate_lemmas.csv``."""
    rows: list[list[str]] = []
    for rank, item in enumerate(candidates, start=1):
        informatives = ",".join(str(batch.batch) for batch in item.informative_batches)
        switches = ",".join(str(batch.batch) for batch in item.switch_batches)
        sequence = "; ".join(
            f"B{batch.batch}:{display_variant(batch.dominant)}"
            for batch in item.informative_batches
        )
        rows.append([
            str(rank),
            item.lemma,
            item.glossary_translation,
            str(item.alternatives_count),
            str(len(item.occurrences)),
            str(len(item.batches)),
            str(len(item.informative_batches)),
            informatives,
            sequence,
            str(item.total_qualifying),
            str(item.switch_occurrences),
            _format_float(item.switch_share),
            str(item.affected_batches),
            switches,
            item.global_dominant,
            str(item.global_counts[item.global_dominant]),
            _format_float(item.global_purity),
            _format_float(item.within_batch_purity),
            _format_float(item.min_batch_purity),
            str(item.distinct_variants),
            str(item.distinct_dominants),
        ])
    return rows


def batch_count_rows(candidates: Sequence[LemmaAnalysis]) -> list[list[str]]:
    """Build rows for ``lemma_batch_counts.csv`` (long format)."""
    rows: list[list[str]] = []
    for item in candidates:
        order = variant_order(item.alternatives)
        for batch in item.batches:
            for variant in sorted(batch.counts, key=lambda v: order.get(v, len(order))):
                rows.append([
                    item.lemma,
                    item.glossary_translation,
                    str(batch.batch),
                    str(batch.sentence_id_min),
                    str(batch.sentence_id_max),
                    str(batch.total),
                    "yes" if batch.informative else "no",
                    _format_float(batch.purity),
                    batch.dominant,
                    str(batch.dominant_count),
                    variant,
                    str(batch.counts[variant]),
                    _format_float(batch.counts[variant] / batch.total),
                ])
    return rows


def occurrence_rows(candidates: Sequence[LemmaAnalysis]) -> list[list[str]]:
    """Build rows for ``lemma_occurrences.csv``."""
    rows: list[list[str]] = []
    for rank, item in enumerate(candidates, start=1):
        batch_lookup = {batch.batch: batch for batch in item.batches}
        for occurrence in item.occurrences:
            batch = batch_lookup[occurrence.batch]
            rows.append([
                str(rank),
                item.lemma,
                item.glossary_translation,
                str(occurrence.sentence_id),
                str(occurrence.batch),
                occurrence.source,
                str(occurrence.definition_index),
                occurrence.variant,
                str(occurrence.variant_index),
                occurrence.dutch_word,
                "yes" if batch.informative else "no",
                batch.dominant,
                _format_float(batch.purity),
                "yes" if batch.dominant != item.global_dominant else "no",
                occurrence.sentence_translation,
            ])
    return rows


def run_dominant(item: LemmaAnalysis) -> str:
    """Return the dominant rendering of a lemma over the whole run.

    Unlike ``item.global_dominant`` (which only counts informative batches),
    this counts every occurrence in the run, with the glossary order as the
    deterministic tie-break.
    """
    counts = Counter(occurrence.variant for occurrence in item.occurrences)
    return dominant_variant(counts, variant_order(item.alternatives))


def inter_batch_lemmata_rows(candidates: Sequence[LemmaAnalysis]) -> list[list[str]]:
    """Build rows for ``inter-batch-lemmata-disambiguation.csv``.

    One row per candidate lemma, in the same order as the report, prefilled with
    the dominant translation over the whole run and empty ``forms``/``note``
    columns.  The reviewer can add form-specific rows (semicolon-separated Latin
    surface forms) when a lemma needs different renderings per form.  When the
    dominant rendering is ``__other__`` (no single glossary alternative
    dominates), the translation is left empty.
    """
    rows: list[list[str]] = []
    for item in candidates:
        dominant = run_dominant(item)
        rows.append([
            item.lemma,
            "" if dominant == OTHER else dominant,
            "",
            "",
        ])
    return rows


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[str]]) -> None:
    """Write a UTF-8 CSV with a fixed header."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def display_variant(variant: str) -> str:
    """Human-readable label for a variant."""
    return OTHER_LABEL if variant == OTHER else variant


def color_map(alternatives: Sequence[str]) -> dict[str, str]:
    """Assign a stable colour to every alternative and to ``other``."""
    colors = {
        alternative: PALETTE[index % len(PALETTE)]
        for index, alternative in enumerate(alternatives)
    }
    colors[OTHER] = "#8a8a8a"
    return colors


def variant_labels(item: LemmaAnalysis) -> list[str]:
    """Return the variants to show, in glossary order followed by other."""
    labels = list(item.alternatives)
    if OTHER in item.global_counts or any(OTHER in batch.counts for batch in item.batches):
        labels.append(OTHER)
    return labels


def svg_batch_chart(item: LemmaAnalysis, width: int = 940) -> str:
    """Render a stacked horizontal bar chart, one bar per batch."""
    row_height = 24
    top = 34
    left = 250
    right = 70
    bar_width = width - left - right
    height = top + len(item.batches) * row_height + 28
    max_total = max(batch.total for batch in item.batches) or 1
    colors = color_map(item.alternatives)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" role="img" aria-label="Batch distribution for {html.escape(item.lemma)}">',
        '<style>.axis{stroke:#d0d0d0;stroke-width:1}.lbl{font:12px system-ui,sans-serif;'
        'fill:#333}.small{font:11px system-ui,sans-serif;fill:#666}</style>',
    ]

    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        x = left + fraction * bar_width
        parts.append(f'<line class="axis" x1="{x:.1f}" y1="{top - 6}" x2="{x:.1f}" y2="{height - 24}"/>')
        parts.append(
            f'<text class="small" x="{x:.1f}" y="{height - 10}" text-anchor="middle">'
            f'{int(round(max_total * fraction))}</text>'
        )
    parts.append(
        f'<text class="small" x="{left + bar_width / 2:.1f}" y="{height - 1}" '
        f'text-anchor="middle">occurrences per batch</text>'
    )

    for index, batch in enumerate(item.batches):
        y = top + index * row_height
        opacity = "1" if batch.informative else "0.35"
        label = f"B{batch.batch} · {batch.sentence_id_min}–{batch.sentence_id_max}"
        if not batch.informative:
            label += " (ignored)"
        parts.append(
            f'<text class="lbl" x="6" y="{y + 15}" opacity="{opacity}">{html.escape(label)}</text>'
        )
        x = left
        for variant in variant_labels(item):
            count = batch.counts.get(variant, 0)
            if not count:
                continue
            segment_width = count / max_total * bar_width
            title = f"B{batch.batch} · {display_variant(variant)}: {count} of {batch.total} ({count / batch.total:.1%})"
            parts.append(
                f'<rect x="{x:.2f}" y="{y + 3}" width="{max(segment_width, 0.5):.2f}" height="15" '
                f'fill="{colors[variant]}" opacity="{opacity}"><title>{html.escape(title)}</title></rect>'
            )
            x += segment_width
        parts.append(
            f'<text class="lbl" x="{left + bar_width + 8}" y="{y + 15}" opacity="{opacity}">{batch.total}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def svg_timeline(item: LemmaAnalysis, run_min: int, run_max: int, width: int = 940) -> str:
    """Render one lane per batch with a tick per occurrence in corpus order."""
    lane_height = 16
    top = 30
    left = 48
    right = 18
    height = top + len(item.batches) * lane_height + 34
    span = max(run_max - run_min, 1)
    colors = color_map(item.alternatives)
    plot_width = width - left - right

    def x_position(sentence_id: int) -> float:
        return left + (sentence_id - run_min) / span * plot_width

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" role="img" aria-label="Corpus timeline for {html.escape(item.lemma)}">',
        '<style>.lbl{font:12px system-ui,sans-serif;fill:#333}.small{font:11px system-ui,sans-serif;'
        'fill:#666}.axis{stroke:#d0d0d0;stroke-width:1}</style>',
    ]

    for fraction in (0.0, 0.5, 1.0):
        sentence_id = int(round(run_min + fraction * span))
        x = left + fraction * plot_width
        parts.append(f'<line class="axis" x1="{x:.1f}" y1="{top - 8}" x2="{x:.1f}" y2="{height - 24}"/>')
        parts.append(
            f'<text class="small" x="{x:.1f}" y="{height - 10}" text-anchor="middle">{sentence_id}</text>'
        )
    parts.append(
        f'<text class="small" x="{left + plot_width / 2:.1f}" y="{height - 1}" '
        f'text-anchor="middle">sentence id (corpus order)</text>'
    )

    for index, batch in enumerate(item.batches):
        y = top + index * lane_height
        opacity = "1" if batch.informative else "0.35"
        parts.append(
            f'<text class="lbl" x="4" y="{y + 10}" opacity="{opacity}">B{batch.batch}</text>'
        )
        parts.append(
            f'<line x1="{x_position(batch.sentence_id_min):.1f}" y1="{y + 9}" '
            f'x2="{x_position(batch.sentence_id_max):.1f}" y2="{y + 9}" '
            f'stroke="#eeeeee" stroke-width="8" opacity="{opacity}"/>'
        )
        for occurrence in batch.occurrences:
            title = (
                f"sentence {occurrence.sentence_id} · B{batch.batch} · "
                f"{display_variant(occurrence.variant)}"
            )
            parts.append(
                f'<rect x="{x_position(occurrence.sentence_id) - 1:.1f}" y="{y + 2}" width="2" '
                f'height="12" fill="{colors[occurrence.variant]}" opacity="{opacity}">'
                f'<title>{html.escape(title)}</title></rect>'
            )

    parts.append("</svg>")
    return "\n".join(parts)


def swatch(color: str) -> str:
    return f'<span class="sw" style="background:{color}"></span>'


def legend_html(item: LemmaAnalysis) -> str:
    colors = color_map(item.alternatives)
    return " ".join(
        f'{swatch(colors[variant])}{html.escape(display_variant(variant))}'
        for variant in variant_labels(item)
    )


def batch_table_html(item: LemmaAnalysis, min_batch_count: int) -> str:
    """Table with one row per batch and one cell with the rendering counts."""
    colors = color_map(item.alternatives)
    body: list[str] = []
    for batch in item.batches:
        counts = " · ".join(
            f'{swatch(colors[variant])}{html.escape(display_variant(variant))}: {batch.counts[variant]}'
            for variant in variant_labels(item)
            if batch.counts.get(variant, 0)
        )
        status = "informative" if batch.informative else f"ignored (&lt; {min_batch_count})"
        body.append(
            "<tr>"
            f"<td>B{batch.batch}</td>"
            f"<td>{batch.sentence_id_min}–{batch.sentence_id_max}</td>"
            f"<td class=\"num\">{batch.total}</td>"
            f"<td>{html.escape(display_variant(batch.dominant))}</td>"
            f"<td class=\"num\">{batch.purity:.0%}</td>"
            f"<td>{counts}</td>"
            f"<td>{status}</td>"
            "</tr>"
        )
    return (
        '<table class="data"><thead><tr><th>Batch</th><th>sentence ids</th>'
        '<th>n</th><th>dominant</th><th>purity</th><th>counts</th><th>status</th>'
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"
    )


def examples_table_html(item: LemmaAnalysis, max_examples: int) -> str:
    """Show examples from batches whose dominant rendering differs."""
    if not item.switch_batches:
        return "<p>No affected batches.</p>"
    colors = color_map(item.alternatives)
    examples: list[Occurrence] = []
    for batch in item.switch_batches:
        examples.extend(batch.occurrences)
    examples.sort(key=lambda occ: occ.sentence_id)
    examples = examples[:max_examples]

    rows: list[str] = []
    for occurrence in examples:
        text = occurrence.sentence_translation
        if len(text) > 360:
            text = text[:357] + "…"
        rows.append(
            "<tr>"
            f"<td>{occurrence.sentence_id}</td>"
            f"<td>B{occurrence.batch}</td>"
            f"<td>{html.escape(occurrence.source)}</td>"
            f"<td>{swatch(colors[occurrence.variant])}"
            f"{html.escape(display_variant(occurrence.variant))}</td>"
            f"<td>{occurrence.definition_index}</td>"
            f"<td>{html.escape(text)}</td>"
            "</tr>"
        )
    return (
        '<table class="data"><thead><tr><th>sentence id</th><th>batch</th>'
        '<th>Latin form</th><th>rendering</th><th>alt. #</th><th>translation</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def lemma_section_html(
    item: LemmaAnalysis,
    rank: int,
    min_batch_count: int,
    run_min: int,
    run_max: int,
    max_examples: int,
) -> str:
    """Render one candidate lemma section."""
    badges = (
        f'<span class="badge">total {len(item.occurrences)}</span>'
        f'<span class="badge">qualifying {item.total_qualifying}</span>'
        f'<span class="badge bad">switched {item.switch_occurrences} ({item.switch_share:.0%})</span>'
        f'<span class="badge">affected batches {item.affected_batches}</span>'
        f'<span class="badge">global purity {item.global_purity:.0%}</span>'
        f'<span class="badge">within-batch purity {item.within_batch_purity:.0%}</span>'
    )
    return f"""
<section class="lemma" id="lemma-{rank}">
  <h2>{rank}. {html.escape(item.lemma)}</h2>
  <p><strong>Glossary:</strong> {html.escape(item.glossary_translation)}
     <span class="muted">({item.alternatives_count} alternatives)</span></p>
  <p class="legend">{legend_html(item)}</p>
  <p>{badges}</p>
  <p class="muted">Global dominant: <strong>{html.escape(display_variant(item.global_dominant))}</strong>
     ({item.global_counts[item.global_dominant]} of {item.total_qualifying} qualifying occurrences).</p>
  <h3>Distribution across batches</h3>
  {svg_batch_chart(item)}
  <h3>Position in the corpus</h3>
  {svg_timeline(item, run_min, run_max)}
  <h3>Batch details</h3>
  {batch_table_html(item, min_batch_count)}
  <details>
    <summary>Examples from affected batches</summary>
    {examples_table_html(item, max_examples)}
  </details>
</section>
"""


def summary_table_html(candidates: Sequence[LemmaAnalysis], rendered_count: int) -> str:
    """Ranked overview table linking to the rendered lemma sections."""
    rows: list[str] = []
    for rank, item in enumerate(candidates, start=1):
        name = html.escape(item.lemma)
        if rank <= rendered_count:
            name = f'<a href="#lemma-{rank}">{name}</a>'
        rows.append(
            "<tr>"
            f"<td class=\"num\">{rank}</td>"
            f"<td>{name}</td>"
            f"<td>{html.escape(item.glossary_translation)}</td>"
            f"<td class=\"num\">{len(item.occurrences)}</td>"
            f"<td class=\"num\">{item.total_qualifying}</td>"
            f"<td class=\"num strong\">{item.switch_occurrences}</td>"
            f"<td class=\"num\">{item.switch_share:.0%}</td>"
            f"<td class=\"num\">{len(item.informative_batches)}</td>"
            f"<td class=\"num\">{item.affected_batches}</td>"
            f"<td>{html.escape(display_variant(item.global_dominant))}</td>"
            f"<td class=\"num\">{item.global_purity:.0%}</td>"
            f"<td class=\"num\">{item.within_batch_purity:.0%}</td>"
            "</tr>"
        )
    return (
        '<table class="data"><thead><tr>'
        '<th>#</th><th>lemma</th><th>glossary alternatives</th><th>total</th>'
        '<th>qualifying</th><th>switched</th><th>switch share</th>'
        '<th>informative batches</th><th>affected batches</th>'
        '<th>global dominant</th><th>global purity</th><th>within-batch purity</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )




def report_html(
    candidates: Sequence[LemmaAnalysis],
    metadata: dict,
    args: argparse.Namespace,
) -> str:
    """Build the complete HTML report."""
    rendered = candidates[:args.max_report_lemmas]
    sections = "\n".join(
        lemma_section_html(
            item,
            rank,
            args.min_batch_count,
            metadata["sentence_id_min"],
            metadata["sentence_id_max"],
            args.max_examples,
        )
        for rank, item in enumerate(rendered, start=1)
    )
    truncated = ""
    if len(candidates) > len(rendered):
        truncated = (
            f'<p class="warn">Only the first {len(rendered)} of {len(candidates)} '
            f'candidates are rendered here. Increase <code>--max-report-lemmas</code> '
            f'or inspect <code>candidate_lemmas.csv</code> and '
            f'<code>lemma_occurrences.csv</code> for the rest.</p>'
        )
    thresholds = (
        f"min total qualifying occurrences <strong>{args.min_total}</strong> · "
        f"min occurrences per informative batch <strong>{args.min_batch_count}</strong> · "
        f"min informative batches <strong>{args.min_batches}</strong> · "
        f"min within-batch purity <strong>{args.min_purity:.0%}</strong> · "
        f"min glossary alternatives <strong>{args.min_alternatives}</strong> · "
        f"min switched occurrences <strong>{args.min_switch_count}</strong>"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Glossary consistency report — {html.escape(Path(metadata['run_dir']).name)}</title>
<style>
  body {{ font: 14px/1.45 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 2rem auto; max-width: 1180px; padding: 0 1rem; color: #222; }}
  h1 {{ margin-bottom: .2rem; }}
  h2 {{ margin-top: 2.5rem; border-top: 2px solid #ddd; padding-top: 1rem; }}
  h3 {{ margin: 1.2rem 0 .4rem; font-size: 1.02rem; }}
  .muted {{ color: #666; }}
  .warn {{ background: #fff4d6; border-left: 4px solid #e0a800; padding: .6rem .8rem; }}
  .badge {{ display: inline-block; background: #eef2f7; border-radius: 10px;
            padding: .12rem .55rem; margin: .1rem .25rem .1rem 0; white-space: nowrap; }}
  .badge.bad {{ background: #fde4e1; color: #8a1f14; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: .8rem; align-items: center; }}
  .sw {{ display: inline-block; width: .72rem; height: .72rem; border-radius: 2px;
         margin-right: .28rem; vertical-align: -1px; }}
  table.data {{ border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; font-size: 13px; }}
  table.data th, table.data td {{ border: 1px solid #ddd; padding: .28rem .45rem; vertical-align: top; }}
  table.data th {{ background: #f4f6f8; text-align: left; }}
  table.data td.num {{ text-align: right; white-space: nowrap; }}
  table.data td.strong {{ font-weight: 700; }}
  details {{ margin: .6rem 0 1rem; }}
  summary {{ cursor: pointer; font-weight: 600; }}
  code, pre {{ background: #f4f6f8; border-radius: 3px; padding: .1rem .25rem; }}
  .method {{ background: #f8fafc; border: 1px solid #e3e8ee; padding: .7rem 1rem; }}
  svg {{ max-width: 100%; height: auto; margin: .2rem 0 .8rem; }}
</style>
</head>
<body>
<h1>Glossary consistency report</h1>
<p class="muted">Run: <code>{html.escape(metadata['run_dir'])}</code></p>
<p>{metadata['sentences_with_glossary']} sentences with glossary entries
   ({metadata['sentences_total']} translated) · {metadata['occurrences']} glossary applications ·
   {metadata['lemmas']} lemmas · {metadata['batch_count']} batches ·
   <strong>{len(candidates)} candidate lemmas</strong></p>

<div class="method">
<strong>How candidates are selected.</strong>
An occurrence is either one of the glossary alternatives (the selected
<code>definition_index</code>) or <em>other</em> when the translation used a
rendering outside the glossary. A batch is informative when it has at least
{args.min_batch_count} occurrences. A lemma is a candidate when every
informative batch is internally consistent (dominant rendering share at least
{args.min_purity:.0%}), at least {args.min_batches} informative batches exist,
the glossary offers at least {args.min_alternatives} alternatives, and at least
{args.min_switch_count} occurrences lie in batches whose dominant rendering
differs from the overall dominant rendering. Candidates are ranked by the
absolute number of switched occurrences, then by their share.
<br><br>Thresholds: {thresholds}
</div>

<h2>Ranked candidates</h2>
{summary_table_html(candidates, len(rendered))}
{truncated}
<p class="muted">CSV outputs: <a href="candidate_lemmas.csv">candidate_lemmas.csv</a>,
<a href="inter-batch-lemmata-disambiguation.csv">inter-batch-lemmata-disambiguation.csv</a>,
<a href="lemma_batch_counts.csv">lemma_batch_counts.csv</a>,
<a href="lemma_occurrences.csv">lemma_occurrences.csv</a>,
<a href="summary.json">summary.json</a>.</p>

<h2>Lemma details</h2>
{sections if sections else '<p>No candidate lemmas passed the thresholds.</p>'}
</body>
</html>
"""


def markdown_report(
    candidates: Sequence[LemmaAnalysis],
    metadata: dict,
    args: argparse.Namespace,
) -> str:
    """Build a compact Markdown summary for quick reading in a terminal/IDE."""
    lines = [
        "# Glossary consistency report",
        "",
        f"Run: `{metadata['run_dir']}`",
        "",
        (
            f"{metadata['sentences_with_glossary']} sentences with glossary entries "
            f"({metadata['sentences_total']} translated) · "
            f"{metadata['occurrences']} glossary applications · "
            f"{metadata['lemmas']} lemmas · {metadata['batch_count']} batches · "
            f"**{len(candidates)} candidate lemmas**"
        ),
        "",
        "## Ranked candidates",
        "",
        "| # | lemma | alternatives | total | qualifying | switched | share | affected batches | dominant sequence |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for rank, item in enumerate(candidates, start=1):
        sequence = "; ".join(
            f"B{batch.batch}:{display_variant(batch.dominant)}"
            for batch in item.informative_batches
        )
        lines.append(
            f"| {rank} | **{item.lemma}** | {item.glossary_translation} "
            f"| {len(item.occurrences)} | {item.total_qualifying} "
            f"| {item.switch_occurrences} | {item.switch_share:.0%} "
            f"| {item.affected_batches} | {sequence} |"
        )
    lines.extend([
        "",
        "## Thresholds",
        "",
        f"- minimum total qualifying occurrences: {args.min_total}",
        f"- minimum occurrences per informative batch: {args.min_batch_count}",
        f"- minimum informative batches: {args.min_batches}",
        f"- minimum within-batch purity: {args.min_purity:.0%}",
        f"- minimum glossary alternatives: {args.min_alternatives}",
        f"- minimum switched occurrences: {args.min_switch_count}",
        "",
        "See `report.html` for per-lemma batch charts, corpus timelines and examples.",
        "",
    ])
    return "\n".join(lines)



def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find lemmas translated consistently within batches but differently across batches"
    )
    parser.add_argument(
        "run_dir",
        nargs="?",
        default="run-glossary",
        help="run directory containing glossary-application-inventory.csv and 30_translation/",
    )
    parser.add_argument(
        "-o", "--output-dir",
        help=f"output directory (default: <run-dir>/{OUTPUT_DIR_NAME})",
    )
    parser.add_argument(
        "--inventory",
        help=f"inventory CSV (default: <run-dir>/{INVENTORY_NAME})",
    )
    parser.add_argument(
        "--translation-dir",
        help="translation directory (default: <run-dir>/30_translation)",
    )
    parser.add_argument(
        "--min-total", type=int, default=20,
        help="minimum qualifying occurrences per lemma (default: 20)",
    )
    parser.add_argument(
        "--min-batch-count", type=int, default=5,
        help="minimum occurrences for a batch to be informative (default: 5)",
    )
    parser.add_argument(
        "--min-batches", type=int, default=3,
        help="minimum number of informative batches (default: 3)",
    )
    parser.add_argument(
        "--min-purity", type=float, default=0.75,
        help="minimum dominant-rendering share per informative batch (default: 0.75)",
    )
    parser.add_argument(
        "--min-alternatives", type=int, default=2,
        help="minimum number of glossary alternatives for a lemma (default: 2)",
    )
    parser.add_argument(
        "--min-switch-count", type=int, default=5,
        help="minimum occurrences in cross-batch switched batches (default: 5)",
    )
    parser.add_argument(
        "--max-report-lemmas", type=int, default=100,
        help="maximum candidate lemmas rendered in report.html (default: 100)",
    )
    parser.add_argument(
        "--max-examples", type=int, default=8,
        help="maximum example sentences per lemma in report.html (default: 8)",
    )
    args = parser.parse_args()

    if not 0 <= args.min_purity <= 1:
        parser.error("--min-purity must be between 0 and 1")

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    run_dir = Path(args.run_dir).resolve()
    inventory_path = Path(args.inventory) if args.inventory else run_dir / INVENTORY_NAME
    if not inventory_path.is_file():
        parser.error(f"inventory not found: {inventory_path}")

    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else run_dir / OUTPUT_DIR_NAME
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    candidates, metadata = analyse_run(run_dir, args)

    write_csv(
        output_dir / "candidate_lemmas.csv",
        [
            "rank", "lemma", "glossary_translation", "alternatives_count",
            "total_occurrences", "batches_total", "batches_informative",
            "informative_batch_numbers", "dominant_sequence", "total_qualifying",
            "switch_occurrences", "switch_share", "affected_batches",
            "affected_batch_numbers", "global_dominant", "global_dominant_count",
            "global_purity", "within_batch_purity", "min_batch_purity",
            "distinct_variants", "distinct_dominants",
        ],
        candidate_rows(candidates),
    )
    write_csv(
        output_dir / "inter-batch-lemmata-disambiguation.csv",
        ["lemma", "translation", "forms", "note"],
        inter_batch_lemmata_rows(candidates),
    )
    write_csv(
        output_dir / "lemma_batch_counts.csv",
        [
            "lemma", "glossary_translation", "batch", "sentence_id_min",
            "sentence_id_max", "occurrences", "informative", "purity",
            "dominant_variant", "dominant_count", "variant", "count", "share",
        ],
        batch_count_rows(candidates),
    )
    write_csv(
        output_dir / "lemma_occurrences.csv",
        [
            "rank", "lemma", "glossary_translation", "sentence_id", "batch",
            "source", "definition_index", "variant", "variant_index",
            "dutch_word", "informative_batch", "batch_dominant", "batch_purity",
            "in_affected_batch", "sentence_translation",
        ],
        occurrence_rows(candidates),
    )

    summary = {
        "metadata": metadata,
        "thresholds": {
            "min_total": args.min_total,
            "min_batch_count": args.min_batch_count,
            "min_batches": args.min_batches,
            "min_purity": args.min_purity,
            "min_alternatives": args.min_alternatives,
            "min_switch_count": args.min_switch_count,
        },
        "candidates": [
            {
                "rank": rank,
                "lemma": item.lemma,
                "glossary_translation": item.glossary_translation,
                "total_occurrences": len(item.occurrences),
                "total_qualifying": item.total_qualifying,
                "switch_occurrences": item.switch_occurrences,
                "switch_share": round(item.switch_share, 6),
                "dominant_sequence": [
                    f"B{batch.batch}:{display_variant(batch.dominant)}"
                    for batch in item.informative_batches
                ],
                "affected_batches": item.affected_batches,
                "global_dominant": display_variant(item.global_dominant),
                "global_purity": round(item.global_purity, 6),
                "within_batch_purity": round(item.within_batch_purity, 6),
            }
            for rank, item in enumerate(candidates, start=1)
        ],
    }
    with (output_dir / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    report = report_html(candidates, metadata, args)
    (output_dir / "report.html").write_text(report, encoding="utf-8")
    (output_dir / "report.md").write_text(
        markdown_report(candidates, metadata, args), encoding="utf-8"
    )

    logger.info("Sentences: %d with glossary entries, %d translated",
                metadata["sentences_with_glossary"], metadata["sentences_total"])
    logger.info("Lemma groups analysed: %d", metadata["lemma_groups"])
    logger.info("Candidate lemmas: %d", len(candidates))
    for rank, item in enumerate(candidates, start=1):
        logger.info(
            "  %2d. %-16s switched %4d/%-4d (%4.0f%%), batches %d, dominant %r",
            rank, item.lemma, item.switch_occurrences, item.total_qualifying,
            100 * item.switch_share, item.affected_batches,
            display_variant(item.global_dominant),
        )
    logger.info("Output written to: %s", output_dir)


if __name__ == "__main__":
    main()

