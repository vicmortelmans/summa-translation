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
