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

`batch_workflow.sh <tsv-file> <start-id> <end-id> <tag> [options]` runs five
stages; every stage writes to its own numbered directory under `run-<tag>/`
(run directories are git-ignored):

| Stage | Script | Input | Output |
| --- | --- | --- | --- |
| 1 | `batch_input.py` | TSV (`latin`, `reference`, `icx`) + id range | `10_input/*.json` |
| 2 | `batch_reference_sentences.py` | `10_input/*.json` | `20_references/*.json` |
| 3 | `batch_glossary.py` | `20_references/*.json` | `25_glossary/*.json` |
| 4 | `batch_translate.py` | `25_glossary/*.json` | `30_translation/*.json` + `30_translation/tokens/` |
| 5 | `batch_merge.py` | `30_translation/*.json` + `30_translation/tokens/*.json` | `40-merge/translations.json`, `40-merge/tokens.json` |

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
   `LatinBackoffLemmatizer`, looks the lemma (falling back to the surface form)
   up in the Axters lexicon, and writes all input fields plus `glossary`:
   a list of `{source, lemma, translation}` (word as in the sentence, CLTK
   lemma, Dutch definition). Duplicate lemmas within a sentence are dropped.
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

## Data structures

Sentence row inside a batch (stages 2–4):

```json
{
  "id": "10626",
  "source": "Latin sentence",
  "references": [{"source": "...", "translation": "..."}],
  "glossary": [{"source": "beatitudinem", "lemma": "beatitudo", "translation": "geluk, zaligheid, gelukzaligheid"}]
}
```

Translation output (`schemas/translate.schema.json`):

```json
{"translations": [{"id": "...", "translation": "..."}]}
```

## Components and directories

- `batch_*.py`, `batch_workflow.sh` — current batch pipeline.
- `prompts/translate.txt` — system prompt; `prompts/archived/` holds old prompts.
- `schemas/translate.schema.json` — OpenAI structured-output schema.
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
