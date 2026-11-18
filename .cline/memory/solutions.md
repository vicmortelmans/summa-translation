# Verified solutions

## S1 — CLTK is not in `requirements.txt`

**Symptom:** `ModuleNotFoundError: No module named 'cltk'` when running
`batch_glossary.py` or `axters-mistral/lemmatize_axters.py` after installing
from `requirements.txt`.

**Cause:** CLTK (1.5.0) is installed manually in `./env` but is not listed in
`requirements.txt`.

**Solution:** use the existing `./env` interpreter (`./env/bin/python`), which
has `cltk` installed. If the environment is rebuilt, install `cltk` explicitly
and fetch the Latin models with `install_lat_models_cltk.py`
(`FetchCorpus(language="lat").import_corpus("lat_models_cltk")`). The
lemmatizer needs those CLTK models.

## S2 — Checking glossary output quickly

The glossary stage can be validated on an existing references batch without
running the full workflow:

```bash
./env/bin/python batch_glossary.py run-test3/20_references/translation_input_batch1_test3.json
./env/bin/python -c "import json; d=json.load(open('run-test3/25_glossary/translation_input_batch1_test3.json')); print(all('glossary' in r for r in d), sum(len(r['glossary']) for r in d))"
```

Expected for `run-test3/20_references/translation_input_batch1_test3.json`
(5 sentences): all rows have a `glossary` field and 27 entries in total.

## S3 — Unsafe parallel verification after a write

When a script writes a file and a follow-up `ls`/`cat` runs as an independent
parallel command, the read can execute before the write finishes and report a
missing file. Run verification commands after the writing command has
completed (or chain them with `&&`) instead of emitting them as parallel
`run_commands`.

## S4 — Lemmatizer token hygiene

`LatinBackoffLemmatizer().lemmatize()` returns punctuation attached when given
raw `split()` output (e.g. `("interiora,", "interiora,")`). `batch_glossary.py`
therefore tokenizes with `re.compile(r"[^\W\d_]+", re.UNICODE)` before
lemmatizing. Keep that filtering if the tokenization is ever changed.

## S5 — Checking glossary resolution/merge behaviour

Run these in `./env/bin/python` after changing the lexicon resolution logic:

```python
from batch_glossary import load_lexicon, build_glossary, merge_definitions, LEXICON_PATH
from cltk.lemmatize.lat import LatinBackoffLemmatizer

by_cltk, by_lemma = load_lexicon(LEXICON_PATH)
lemmatizer = LatinBackoffLemmatizer()

assert merge_definitions(["a, b, c", "d, e"]) == ["d", "a", "e", "b", "c"]
assert build_glossary("agit", lemmatizer, by_cltk, by_lemma, {})[0]["definitions"] == [
    "handelen", "daad", "werkend", "doen", "daadwerkelijkheid",
    "handelend", "act", "doener", "dader", "werkende oorzaak",
]
# exact headword wins and suppresses the merged `ago` group, in either order
assert [g["source"] for g in build_glossary("agit actus", lemmatizer, by_cltk, by_lemma, {})] == ["actus"]
assert [g["source"] for g in build_glossary("actus agit", lemmatizer, by_cltk, by_lemma, {})] == ["actus"]
# distinct headwords sharing CLTK lemma `utor` are both kept
assert [g["source"] for g in build_glossary("usura et usus", lemmatizer, by_cltk, by_lemma, {})] == ["usura", "usus"]
# surface headword beats the CLTK lemma (`malum` vs `malus`)
assert build_glossary("malum", lemmatizer, by_cltk, by_lemma, {})[0]["definitions"] == ["kwaad"]
```

Note that `batch_glossary.py` caches lemmatizations across calls; pass a fresh
`{}` when a test sentence shares words with a previous call, or reuse the cache
deliberately.

For output-shape compatibility, run the inventory on an old and a new run:

```bash
./env/bin/python glossary_application_inventory.py run-glossary -o /tmp/inventory-legacy.csv
diff /tmp/inventory-legacy.csv run-glossary/glossary-application-inventory.csv
```

The two files must be identical (the helper falls back to splitting the legacy
`translation` string). A new-shape run needs `25_glossary/batch<N>_glossary.json`
and `30_translation/batch<N>_glossary.json` files; its `translation` CSV column
is the `definitions` list joined with `", "`.

## S6 — Running the glossary analysis stage manually

Stage 6 of `batch_workflow.sh` consists of two commands:

```bash
./env/bin/python glossary_application_inventory.py "run-<tag>"
./env/bin/python analysis-glossary-consistency/scripts/analyse_glossary_consistency.py "run-<tag>"
```

The inventory must be written to its default location
(`<run>/glossary-application-inventory.csv`) because the analysis script has no
option for an alternative inventory path. It reads every `25_glossary/*.json`
(any tag) and the `{"translations": [...]}` files in `30_translation/`; the
analysis then writes `<run>/glossary-consistency/` with `report.md`,
`report.html`, `candidate_lemmas.csv`,
`inter-batch-lemmata-disambiguation.csv` (columns `lemma`, `translation`, the
latter prefilled with the dominant rendering over all occurrences of the lemma),
`lemma_batch_counts.csv`, `lemma_occurrences.csv` and `summary.json`.

On `run-glossary` this reproduces the existing report with 5 candidates
(`beatitudo`, `operatio`, `philosophus`, `opus`, `accido1`). On a tiny run the
report is still generated, possibly with 0 candidates.

## S7 — Old runs and a changed Axters/CLTK lexicon

**Symptom:** replaying stage-3 resolution on an old run (e.g. `run-glossary`)
finds no CLTK key for `accido1`; the current lexicon has `cltk: accido`, while
the run's glossary entries carry `lemma: accido1`.

**Cause:** the lexicon and/or CLTK model changed after the run was produced.

**Solution:** `terminology_forms.py` anchors form lookup on the lemma recorded in
the run's own glossary entry and only re-lemmatizes the Latin sentence; it does
not re-resolve the lemma against the current lexicon. If no token matches, the
recorded `source` form is used as a safety net. Keep this behaviour when
changing form enumeration; re-resolving silently drops occurrences.

## S8 — Strict vs derivational term matching

**Symptom:** with `glossary_application_inventory.variants()`, “de gelukzalige”
counts as “gelukzaligheid” (the `-heid` root rule), so 18 beatitudo sentences
were falsely classified `mixed`; “bijkomstig” counts as “bijkomstigheid”.

**Solution:** terminology planning/verification uses
`terminology_common.strict_variants()`, which only inflects the whole word
(plural/definite/spelling changes) and does not strip derivational suffixes.
The inventory and consistency analysis keep their original, looser matching for
backward compatibility. Expect the plan to select a few more sentences than the
inventory found (e.g. 39 vs 19 for accido1 without form rows, 42 with the verb
split); the model can decline a form with `applied=false` and a reason (verbal
use, different part of speech).

## S9 — Testing the terminology workflow without spending API calls

```bash
# 1. validate the review file and print plan counts
./terminology_workflow.sh glossary --review-csv /tmp/review.csv --dry-run

# 2. generate requests, then fabricate revision files from them
./env/bin/python terminology_plan.py run-glossary --review-csv /tmp/review.csv \
    --terminology-dir /tmp/term --lemma philosophus
# write /tmp/term/20_review/philosophus/part01.json as
# {"translations": [{"id": ..., "translation": ..., "terms": [...]}, ...]}

# 3. apply, then inventory/analyse/verify the corrected layer
./env/bin/python terminology_apply.py run-glossary --terminology-dir /tmp/term \
    --lemma philosophus --input-layer run-glossary/30_translation \
    --output-layer /tmp/term/30_translation
./env/bin/python glossary_application_inventory.py run-glossary \
    --translation-dir /tmp/term/30_translation \
    -o /tmp/term/50_verification/glossary-application-inventory.csv
./env/bin/python analysis-glossary-consistency/scripts/analyse_glossary_consistency.py \
    run-glossary --inventory /tmp/term/50_verification/glossary-application-inventory.csv \
    --translation-dir /tmp/term/30_translation -o /tmp/term/50_verification/glossary-consistency
./env/bin/python terminology_verify.py run-glossary --terminology-dir /tmp/term
```

The workflow reuses existing revision files unless `--force` is passed; after
editing the reviewed CSV, pass `--force` so stale model output is not applied.

