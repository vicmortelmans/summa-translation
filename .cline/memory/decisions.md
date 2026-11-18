# Decisions

Decisions are current unless marked superseded.

## D1 — Pipeline stage order and directory numbering

**Decision:** the workflow runs `batch_input.py` → `batch_reference_sentences.py`
→ `batch_glossary.py` → `batch_translate.py` → `batch_merge.py`, followed by
`glossary_application_inventory.py` and
`analysis-glossary-consistency/scripts/analyse_glossary_consistency.py`.
Outputs are `10_input`, `20_references`, `25_glossary`, `30_translation`,
`40-merge`, plus `glossary-application-inventory.csv` and `glossary-consistency/`
at the run root (including `inter-batch-lemmata-disambiguation.csv`).

**Rationale:** `25_glossary` sits between references and translation so the
translation stage receives both `references` and `glossary` in its input, while
the numbering leaves room for future stages. The glossary stage consumes the
reference output rather than the raw input because references are produced
first and are preserved through the glossary stage. `batch_merge.py` combines
all translation batches and token counts into one job-level result, and the
final inventory/consistency stage reports how the glossary was applied and
where terminology drifted between batches.

**Alternatives:** inserting the glossary before references or merging it into
`batch_reference_sentences.py`; rejected to keep each script single-purpose and
independently callable. The analysis stage was initially run manually on
`run-glossary`; it is now part of the workflow so every run gets the report.

## D2 — Glossary entry shape

**Decision:** each glossary entry is
`{"source": <word as in the sentence>, "lemma": <CLTK lemma>, "definitions":
[<Dutch definition>, ...]}`. Entries are de-duplicated per sentence by the
resolved lexicon headword for exact matches, and by CLTK lemma for merged
groups, keeping the first occurring word form. `definitions` is the ordered
list of Axters senses of the matched entry, or the merged list when a CLTK
lemma is ambiguous; its first element is the preferred definition.

**Rationale:** the surface form is what the translator sees in the sentence,
the lemma identifies the term and enables consistency across inflections, and
the ordered list makes both the sense boundaries and the first-definition
preference explicit to the model. De-duplicating by resolved headword keeps
distinct headwords that share a CLTK lemma (e.g. `usura` and `usus`, both
`utor`) as separate entries.

**Superseded:** an earlier version used the lexicon headword as `source` and had
no `lemma` field; replaced on explicit user request. A later version stored a
comma-separated `translation` string; replaced by the `definitions` list. That
string remains the legacy shape accepted by the analysis scripts for existing
runs.

## D3 — Lexicon lookup and ambiguity resolution

**Decision:** load `axters-mistral/axters-scholastiek-lexicon-1937-cltk.json`
(path resolved relative to the script) and build two case-insensitive indexes:
`by_lemma` (headword → its single entry) and `by_cltk` (CLTK lemma → *all*
entries sharing it). Parenthetical parts of lemmas (`abstinere (se)`) are
removed before indexing/matching. Per token, resolution is:

1. **Exact headword match** — the word as it appears in the sentence equals a
   lexicon `lemma` → use that entry only.
2. **Exact lemmatized headword match** — the CLTK lemma equals a lexicon
   `lemma` → use that entry only.
3. **CLTK fallback** — the CLTK lemma matches a `cltk` key: one entry → use it;
   several entries → merge all their definitions, because none is primary.
4. **Surface-form CLTK fallback** — same single/merge treatment for the surface
   form; otherwise the word is omitted.

Within one sentence, an exact headword match suppresses the merged entry for
the same CLTK lemma, regardless of word order. Distinct exact headwords that
share a CLTK lemma remain separate entries.

**Merging:** split each definition on commas only (`/`, `;`, `:` stay inside a
sense), sort the lists stably by length (shortest first, ties in lexicon file
order), then take item *i* from each list round-robin, dropping duplicate
senses case-insensitively. The result is the ordered `definitions` list.
Example: `["a, b, c", "d, e"]` → `["d", "a", "e", "b", "c"]`; for `ago`
(`agere` 2, `actus` 3, `agens` 5) this yields
`["handelen", "daad", "werkend", "doen", "daadwerkelijkheid", "handelend",
"act", "doener", "dader", "werkende oorzaak"]`.

**Rationale:** the lexicon's `cltk` field is itself produced by
`LatinBackoffLemmatizer`, but an exact headword is stronger evidence than the
CLTK umbrella lemma. When only the umbrella lemma matches, there is no basis to
call one entry primary, so all senses are offered; the prompt then biases
towards the first (shortest list's first sense) and requires cross-sentence
consistency.

**Data facts:** 3,351 unique normalized lemmas (no duplicate headwords) and 168
CLTK keys shared by more than one lemma. Merging therefore mixes senses for
common verbs, e.g. `sum` (`esse`, `futurum`, `futurus`) →
`toekomstig, zijn, toekomst, aanzijn, het toekomstige`, and `dico` (`dicere`,
`dictum`) → `zegging, zeggen, verwoorden`.

**Known limitation:** lookups only match exact normalized forms; multi-word
lexicon keys and lemmatizer outputs absent from the lexicon are skipped (e.g.
`principia`, `interiora` in the sample sentence were not matched).

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

## D7 — Terminology harmonisation workflow

**Decision:** after a human reviews
`glossary-consistency/inter-batch-lemmata-disambiguation.csv`, a second,
independent workflow applies the decisions:
`terminology_workflow.sh <tag>` runs `terminology_plan.py` → `batch_translate.py`
(unchanged) → `terminology_apply.py`, lemma by lemma in review-file order, then
`batch_merge.py`, the stage-6 inventory/analysis on the corrected layer, and
`terminology_verify.py`. All artefacts live in `run-<tag>/50-terminology/`
(`10_plan`, `20_review`, `30_translation`, `40_changes`, `40-merge`,
`50_verification`); stages 1–5 of the preliminary run are never modified.

**Review CSV contract:** `lemma,translation,forms,note`. `forms` is a
semicolon-separated list of Latin surface forms; empty means the lemma-wide
default, and a form may belong to only one row per lemma. This lets a human give
different renderings for different senses of one lemma (e.g. noun `accidens` →
`bijkomstigheid`, verb `accidit` → `gebeuren`) without a per-form file.
`lemma_form_inventory.csv`, written by the new stage 7 of `batch_workflow.sh`,
lists every form with counts (the glossary stores only the first form per
sentence and lemma). `--review-csv` prefers the `.reviewed.csv` copy so a later
analysis run cannot overwrite human decisions. `exclude_ids` was dropped: form
rows plus the model's per-sentence exception mechanism cover the same cases more
readably.

**Per-item prompt content:** the Latin source, the current Dutch translation,
the target lemma entry (forms, preferred rendering, glossary definitions, 2
preferred-translation examples from the same run) and `locked_terms` from
earlier lemma passes. Corpus `references` are deliberately not sent: for
`beatitudo` they use "geluk" in 285 of 413 sentences and would pull the model
back to the inconsistent term. Only sentences whose rendering differs from the
preferred term are reviewed (304 instead of 721 in `run-glossary`, 307 with the
`accido1` verb split); the goal is harmonisation, not retranslation.

**Selection and verification:** form matching is anchored on the lemma recorded
in the run's own glossary, not re-resolved against the current lexicon, because
the Axters/CLTK lexicon has changed since `run-glossary` was produced
(`accido` vs `accido1`). Preferred-term matching uses whole-word inflections
(`terminology_common.strict_variants`), not the inventory's derivational stems,
so "de gelukzalige" is not counted as "gelukzaligheid". Sentences already
containing the preferred term plus another glossary alternative are classified
`mixed` and reviewed; the plan warns about uncovered forms.

**Rationale:** the review is a narrow terminology edit, so one term per request,
minimal edits, explicit exception reporting (`applied=false` + reason), smaller
requests re-batched per lemma, and an auditable corrected layer are preferable
to re-running the translation or forcing every occurrence. Sequential
per-lemma application lets later passes see earlier corrections and lock them;
only three sentences in `run-glossary` contain two non-conforming candidates.

