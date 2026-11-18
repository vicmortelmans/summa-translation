# Architecture

## Purpose

Translate Latin sentences of Thomas Aquinas' *Summa Theologiae* into Dutch
using a batch pipeline. Each sentence is translated by an LLM (OpenAI Responses
API) with two kinds of supporting context:

- **references**: semantically similar Latin sentences with existing Dutch
  translations,
- **glossary**: Dutch definitions of the Latin words in the sentence, taken from
  the Axters scholastic lexicon.

## Pipeline

`batch_workflow.sh <tsv-file> <start-id> <end-id> <tag> [options]` runs six
stages; stages 1–5 write to numbered directories under `run-<tag>/` (run
directories are git-ignored), stage 6 writes the analysis outputs at the run
root:

| Stage | Script | Input | Output |
| --- | --- | --- | --- |
| 1 | `batch_input.py` | TSV (`latin`, `reference`, `icx`) + id range | `10_input/*.json` |
| 2 | `batch_reference_sentences.py` | `10_input/*.json` | `20_references/*.json` |
| 3 | `batch_glossary.py` | `20_references/*.json` | `25_glossary/*.json` |
| 4 | `batch_translate.py` | `25_glossary/*.json` | `30_translation/*.json` + `30_translation/tokens/` |
| 5 | `batch_merge.py` | `30_translation/*.json` + `30_translation/tokens/*.json` | `40-merge/translations.json`, `40-merge/tokens.json` |
| 6 | `glossary_application_inventory.py`, `analysis-glossary-consistency/scripts/analyse_glossary_consistency.py` | `25_glossary/*.json` + `30_translation/*.json` | `glossary-application-inventory.csv`, `glossary-consistency/` (incl. `inter-batch-lemmata-disambiguation.csv`) |
| 7 | `terminology_form_inventory.py` | `glossary-consistency/` + `25_glossary/*.json` | `glossary-consistency/lemma_form_inventory.csv` |

Stage 7 lists every Latin surface form of the candidate lemmas with occurrence
counts, so the human reviewer can add form-specific rows to the disambiguation
CSV. After the review, `terminology_workflow.sh` (see below) applies the
decisions in a separate `50-terminology/` tree; it never modifies stages 1–5.

Each batch file keeps the input filename. Stage scripts also append their own
command line to a log file in their output directory
(`batch_input_command.txt`, `batch_reference_sentences_command.txt`,
`batch_glossary_command.txt`, `batch_translate_command.txt`,
`batch_merge_command.txt`; the last two are written by `batch_translate.py`
and `batch_merge.py`).

### Stage details

1. `batch_input.py` — reads TSV rows in `--start-id`/`--end-id` (inclusive,
   1-based data rows) and writes batches of `--batch-size` (default 500) rows
   with fields `id`, `source`, `reference`, `sequence`.
2. `batch_reference_sentences.py` — calls `vector.search.find_similar()` for
   each sentence and writes `id`, `source`, `references` (each
   `{source, translation}`; `--reference-number`, default 5).
3. `batch_glossary.py` — lemmatizes each word of `source` with CLTK
   `LatinBackoffLemmatizer` and resolves it against the Axters lexicon: an
   exact headword match (surface form first, then lemma) uses that entry only;
   otherwise the CLTK lemma's entries are used, merging their definitions when
   several share the lemma. It writes all input fields plus `glossary`:
   a list of `{source, lemma, definitions}` (word as in the sentence, CLTK
   lemma, ordered list of Dutch definitions). Exact headword matches suppress
   the merged entry for the same CLTK lemma within a sentence; duplicate terms
   are dropped.
4. `batch_translate.py` — sends the batch items as JSON under key `items` to the
   OpenAI Responses API with a JSON schema (`schemas/translate.schema.json`)
   and the system prompt from `--system-prompt` (default
   `prompts/translate.txt`). It retries once on `ValueError`, requires the
   returned `id`s to equal the input `id`s exactly and in order, writes the
   model output as-is (not merged with the input) and stores token usage under
   `tokens/` in the output directory.
5. `batch_merge.py` — merges all `30_translation/*.json` files in natural
   filename order into `40-merge/translations.json` (a single
   `{"translations": [...]}` object), sums every token category from
   `30_translation/tokens/*.json` into `40-merge/tokens.json`, and appends its
   command line to `40-merge/batch_merge_command.txt`. It rejects duplicate
   translation ids across batches.
6. `glossary_application_inventory.py` — for every sentence, compares each
   glossary entry's definitions with the Dutch translation and writes
   `glossary-application-inventory.csv` (one row per glossary entry). Then
   `analysis-glossary-consistency/scripts/analyse_glossary_consistency.py`
   reads that inventory plus `30_translation/*.json` and writes
   `glossary-consistency/` (`report.md`, `report.html`, CSVs, `summary.json`).
   Among the CSVs, `inter-batch-lemmata-disambiguation.csv` lists each candidate
   lemma with `lemma` and `translation`, the latter prefilled with the dominant
   translation over the whole run (every occurrence; blank when the dominant
   rendering is `__other__`).
   Neither script writes a command log. The inventory accepts both the current
   `definitions` list and the legacy comma-separated `translation` string, and
   globs every `25_glossary/*.json` so it works for any run tag.

## Terminology harmonisation workflow

`terminology_workflow.sh <tag>` is a second entry point, run after a human has
edited `glossary-consistency/inter-batch-lemmata-disambiguation.csv` (save it as
`...reviewed.csv`). It writes only under `run-<tag>/50-terminology/`:

| Stage | Script | Output |
| --- | --- | --- |
| plan | `terminology_plan.py` | `10_plan/` (`reviewed.csv`, `plan/<lemma>.csv`, `requests/<lemma>/partNN.json`, `summary.json`) |
| review | `batch_translate.py` + `schemas/terminology_review.schema.json` + `prompts/terminology-review.txt` | `20_review/<lemma>/partNN.json` + `tokens/` |
| apply | `terminology_apply.py` | `30_translation/batch<N>.json`, `40_changes/` (`changes.csv`, `exceptions.csv`, `summary/`, `report.md`) |
| merge | `batch_merge.py` | `40-merge/translations.json`, `tokens.json` |
| verify | `glossary_application_inventory.py`, `analyse_glossary_consistency.py`, `terminology_verify.py` | `50_verification/` (`glossary-application-inventory.csv`, `glossary-consistency/`, `comparison.csv`, `report.md`) |

Lemmas are processed sequentially in review-file order: plan → one
`batch_translate.py` call per part → apply. The next lemma is planned against
the corrected layer and receives `locked_terms` for terms already harmonised in
the same sentence. `--dry-run` only validates the CSV and prints counts,
`--force` redoes existing revisions, `--reset` removes `50-terminology/`.

Review CSV: `lemma,translation,forms,note`; `forms` is a semicolon-separated
list of Latin surface forms (empty = lemma-wide default). The plan enumerates
all forms from the run's own glossary lemmas with `terminology_forms.py` and
maps them to form-specific or default rows. Only non-conforming sentences
(`missing` or `mixed`) are sent; model items contain the Latin source, current
translation, target terms (forms, preferred rendering, definitions, examples)
and locked terms. Output terms carry `applied`, `rendering` and `reason`;
declined terms land in `exceptions.csv` and are legitimate.

`terminology_common.py` holds the reviewed-CSV parser, run loaders, strict
whole-word matching (`strict_variants`) and text helpers;
`terminology_forms.py` wraps the CLTK lemmatizer and anchors form lookup on the
glossary's recorded lemma (not on the current lexicon).

## Data structures
## Data structures

Sentence row inside a batch (stages 2–4):

```json
{
  "id": "10626",
  "source": "Latin sentence",
  "references": [{"source": "...", "translation": "..."}],
  "glossary": [{"source": "beatitudinem", "lemma": "beatitudo", "definitions": ["geluk", "zaligheid", "gelukzaligheid"]}]
}
```

Glossary files written before the `definitions` change store a comma-separated
`translation` string instead; analysis scripts accept both shapes.

Translation output (`schemas/translate.schema.json`):

```json
{"translations": [{"id": "...", "translation": "..."}]}
```

## Components and directories

- `batch_*.py`, `batch_workflow.sh` — current batch pipeline.
- `terminology_*.py`, `terminology_workflow.sh` — terminology harmonisation
  after the human review (`terminology_common`, `terminology_forms`,
  `terminology_form_inventory`, `terminology_plan`, `terminology_apply`,
  `terminology_verify`).
- `glossary_application_inventory.py` — glossary-vs-translation inventory for
  a finished run (stage 6 of the workflow).
- `analysis-glossary-consistency/` — scripts and reports for the glossary
  consistency analysis (`scripts/analyse_glossary_consistency.py`, stage 6).
- `analysis-20261115-terminology/` — older terminology analysis with its own
  scripts, tables and reports.
- `prompts/translate.txt` — system prompt; `prompts/archived/` holds old prompts;
  `prompts/terminology-review.txt` is the terminology review prompt.
- `schemas/translate.schema.json` — OpenAI structured-output schema;
  `schemas/terminology_review.schema.json` for terminology revisions.
- `vector/` — `build_database.py` (embeddings → Qdrant) and `search.py`
  (`find_similar`, model `BAAI/bge-m3`, local Qdrant store `vector/vector_db`,
  collection `latin_sentences`). Reads `HF_TOKEN` from environment or `.hf_token`.
- `bm25/` — alternative lexical retrieval (BM25 index over `latin_nl_align.txt`),
  not used by the current batch workflow.
- `axters-mistral/` — source and generated lexicon files:
  `axters-scholastiek-lexicon-1937.json` (lemma, definition),
  `axters-scholastiek-lexicon-1937-cltk.json` (lemma, cltk, definition) and
  `lemmatize_axters.py`, which builds the CLTK version.
- `align/` — hunalign-based alignment of the Latin/Dutch TEI corpus, producing
  `latin_nl_align.txt` used by the vector and BM25 indexes.
- `index-thomisticus/`, `create_db.py`, `model.sql`, `load_latin.py`,
  `load_dutch.py`, `extract_terms.py`, `ai.py`, `translate_list.py`,
  `test_translate_list.py`, `translation_compare.py` — older/auxiliary
  database and comparison tooling; `translate_list.py` is marked as merged into
  `test_translate_list.py`.

## Environment and dependencies

- Interpreter and packages live in `./env` (Python 3.12.3); run scripts as
  `./env/bin/python ...`.
- `requirements.txt` lists the Python dependencies, but **does not include
  `cltk`**; CLTK 1.5.0 is installed manually in `env` (needed by
  `batch_glossary.py` and `axters-mistral/lemmatize_axters.py`).
- OpenAI access: `OPENAI_API_KEY`, else `.openai_api_key` in the repo root
  (git-ignored).
- Hugging Face access for the embedding model: `HF_TOKEN`, else `.hf_token`
  (git-ignored).
- `.gitignore` excludes secrets, `env/`, `run*/`, `batch*/`, `bm25/`, `*.db`,
  `*.docx`.

## Conventions

- Scripts are standalone CLIs with `argparse`, `logging.basicConfig(...INFO)` and
  a module-level `logger`.
- Output directories are derived from the input file path
  (`Path(filepath).resolve().parent.parent / "<stage dir>"`).
- Stage scripts preserve the input objects and add their own field
  (`references`, `glossary`); `batch_translate.py` is the exception: it writes
  only the model output.
- JSON is written with `ensure_ascii=False, indent=2`.
