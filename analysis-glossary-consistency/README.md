# Glossary consistency analysis

Find lemmas that are translated consistently **within** each batch but with a
different dominant rendering **between** batches. These are candidates for
human review and a later cross-batch harmonisation pass.

## Reproduce

Run from the project root:

```bash
./env/bin/python analysis-glossary-consistency/scripts/analyse_glossary_consistency.py run-glossary
```

The input is `<run>/glossary-application-inventory.csv`; sentence translations
are read from `<run>/30_translation/*.json`. Output goes to
`<run>/glossary-consistency/` by default (use `-o DIR` to change it).  For a
corrected layer, pass `--inventory CSV` and `--translation-dir DIR` (as
`terminology_workflow.sh` does).

| File | Contents |
| --- | --- |
| `candidate_lemmas.csv` | Ranked triage list with all metrics |
| `inter-batch-lemmata-disambiguation.csv` | One row per candidate: `lemma,translation,forms,note`; review this file |
| `lemma_form_inventory.csv` | Every Latin form of every candidate, with counts (written by `terminology_form_inventory.py`) |
| `lemma_batch_counts.csv` | Long table: lemma × batch × rendering counts, purity, sentence-id range |
| `lemma_occurrences.csv` | Every occurrence of every candidate lemma, with sentence, batch, Latin form and translation |
| `report.html` | Human-readable report: ranked summary, stacked batch charts, corpus timelines, batch tables and examples |
| `report.md` | Compact ranked Markdown summary for quick reading |
| `summary.json` | Thresholds, run totals and candidate metrics |

## Reviewing and applying the decisions

`inter-batch-lemmata-disambiguation.csv` is the input of the terminology
harmonisation pass.  Fill in `translation` for the lemmas to harmonise; leave a
row blank or delete it to skip that lemma.  The optional `forms` column applies
a rendering to specific Latin surface forms only and is needed when one lemma
has several senses:

```csv
lemma,translation,forms,note
accido1,bijkomstigheid,accidens;accidentia;accidentis;accidenti;accidentibus,
accido1,gebeuren,accidit;accidunt;accidat;accidere,verbal use
```

An empty `forms` value is the lemma-wide default; a form may appear in only one
row per lemma.  Consult `lemma_form_inventory.csv` for the available forms and
their counts.  Save the reviewed file as
`inter-batch-lemmata-disambiguation.reviewed.csv` so a later analysis run cannot
overwrite it, then run:

```bash
./terminology_workflow.sh <tag>
```

The workflow writes everything to `run-<tag>/50-terminology/`: plan, review
requests and model output, a corrected translation layer, change/exception
reports, merged translations, and a before/after verification under
`50_verification/`.  `--dry-run` validates the CSV and prints the planned
counts without calling the API.


## Method

Each inventory row is reduced to a **rendering**:

- the selected glossary alternative (the text at `definition_index`), or
- `other` when `definition_index = 0` (the translator used a word outside the
  glossary).

For each lemma (and glossary definition list):

1. Aggregate occurrences per batch.
2. Keep **informative batches** with at least `--min-batch-count` occurrences
   (default 5). Smaller batches are shown but do not drive the decision.
3. A lemma is a **candidate** when:
   - the glossary has at least `--min-alternatives` alternatives (default 2,
     matching the *beatitudo* situation);
   - there are at least `--min-batches` informative batches (default 3);
   - every informative batch is internally consistent: dominant rendering
     share ≥ `--min-purity` (default 0.75);
   - the informative batches contain at least `--min-total` occurrences
     (default 20);
   - at least `--min-switch-count` occurrences (default 5) lie in batches whose
     dominant rendering differs from the overall dominant rendering.

Candidates are ranked by **absolute switched occurrences**, then by switch
share. The absolute count is the primary key: a switch affecting hundreds of
occurrences matters more than the same proportion in a rare lemma.

"Switched occurrences" are all occurrences in informative batches whose batch
dominant differs from the global dominant. This measures the size of the
harmonisation decision, not just the number of batches involved.

## Report

`report.html` contains, per candidate lemma:

- the glossary alternatives and a colour legend;
- a stacked bar per batch, labelled with its sentence-id range, showing counts
  per rendering (faded bars are below the informative-batch threshold);
- a corpus timeline with one lane per batch and one tick per occurrence, so
  clusters and within-batch positions are visible;
- a batch table with counts, dominant rendering, purity and status;
- example sentences from affected batches.

Only the first `--max-report-lemmas` candidates (default 100) are rendered in
HTML; the CSV files always contain the full set. This keeps production reports
manageable when the number of candidates grows.

## Threshold tuning

All thresholds are CLI options. Useful starting points:

- stricter, high-confidence triage: `--min-purity 0.9 --min-batch-count 10 --min-switch-count 10`;
- broader inventory: `--min-alternatives 1 --min-purity 0.7`.

The defaults are calibrated on the 4,409-sentence `run-glossary` sample and
produce 5 candidates. Production runs will usually need higher absolute
thresholds (`--min-total`, `--min-batch-count`, `--min-switch-count`) while
keeping the purity criterion.

## Caveats

- `__other__` (shown as “other (not in glossary)” in the HTML report) groups
  all non-glossary renderings; it is not a specific Dutch word. The batch tables
  and examples show what was actually written.
- Rendering detection uses the inventory's conservative inflection matching.
  A missed inflection appears as `other`; inspect examples before deciding.
- The analysis is descriptive. It selects candidates for human review, but does
  not decide which alternative should be preferred globally.
