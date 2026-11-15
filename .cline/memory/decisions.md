# Decisions

Decisions are current unless marked superseded.

## D1 — Pipeline stage order and directory numbering

**Decision:** the workflow runs `batch_input.py` → `batch_reference_sentences.py`
→ `batch_glossary.py` → `batch_translate.py`, with output directories
`10_input`, `20_references`, `25_glossary`, `30_translation`.

**Rationale:** `25_glossary` sits between references and translation so the
translation stage receives both `references` and `glossary` in its input, while
the numbering leaves room for future stages. The glossary stage consumes the
reference output rather than the raw input because references are produced
first and are preserved through the glossary stage.

**Alternatives:** inserting the glossary before references or merging it into
`batch_reference_sentences.py`; rejected to keep each script single-purpose and
independently callable.

## D2 — Glossary entry shape

**Decision:** each glossary entry is
`{"source": <word as in the sentence>, "lemma": <CLTK lemma>, "translation":
<Dutch definition>}`. Entries are de-duplicated per sentence by lemma, keeping
the first occurring word form. The definition is the Axters `definition` of the
matched entry.

**Rationale:** the surface form is what the translator sees in the sentence,
the lemma identifies the term and enables consistency across inflections, and
the definition is the glossary content. De-duplicating by lemma avoids
repeating the same definition for several inflections of one word.

**Superseded:** an earlier version used the lexicon headword as `source` and had
no `lemma` field; replaced on explicit user request.

## D3 — Lexicon lookup strategy

**Decision:** load `axters-mistral/axters-scholastiek-lexicon-1937-cltk.json`
(path resolved relative to the script) and build two case-insensitive indexes:
by `cltk` and by `lemma`. Lookup order per token: lemmatized form against
`by_lemma`, then `by_cltk`, then the surface form against both. Parenthetical
parts of lemmas (`abstinere (se)`) are removed before indexing/matching. When
several entries share a key in the `cltk` index, the entry whose own lemma
equals that key wins (e.g. `agere` rather than `actus` for CLTK lemma `ago`).

**Rationale:** the lexicon's `cltk` field is itself produced by
`LatinBackoffLemmatizer`, but matching the lemmatized token against lexicon
headwords first gives the most natural headword; the surface-form fallback
recovers words the lemmatizer leaves unchanged.

**Known limitation:** the lookups only match exact normalized forms; multi-word
lexicon keys and lemmatizer outputs that are not in the lexicon are skipped.

## D4 — Prompt treatment of the glossary

**Decision:** `prompts/translate.txt` instructs the model to use glossary
entries, to bias towards the first definition when several are listed, to
deviate only when the context clearly indicates a better one, and in that case
to align with the definition choice made in immediately preceding/following
sentences that use the same word (same lemma).

**Rationale:** the Axters lexicon lists definitions roughly in order of
importance; consistency of terminology across neighbouring sentences matters
more than per-sentence optimisation. Ensure the prompt is passed via
`--system-prompt` so the glossary field in the input is used.

## D5 — Command logging per stage

**Decision:** each stage appends its quoted command line (`shlex.quote` over
`sys.argv`) to a command file in its output directory; glossary and reference
stages append, `batch_input.py` (re)writes it.

**Rationale:** reproducible run logs per stage without a central log.

## D6 — Translation output is not merged with input

**Decision:** `batch_translate.py` writes only the model output
(`{"translations": [...]}`) and token usage; it does not merge translations back
into the input batch.

**Rationale:** keeps the model output auditable and separate from the input
batch; consumers can combine files explicitly when needed.
