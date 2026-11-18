# Project memory index

Knowledge base for the `summa-translation` project: a Latin-to-Dutch machine
translation pipeline for Thomas Aquinas' *Summa Theologiae*.

| Document | Contents | Consult when |
| --- | --- | --- |
| `architecture.md` | Components, data flow, directory layout, dependencies, conventions | Before changing scripts, pipeline stages or data formats |
| `decisions.md` | Significant technical/functional decisions and rationale | Before changing batch stages, output schemas, glossary behaviour or prompts |
| `solutions.md` | Verified solutions to problems encountered | When hitting similar errors or data-quality issues |
| `current-state.md` | Implementation status, known limitations, outstanding work | At the start of a session, to know what exists and what is open |

Related project artifacts outside `.cline/`:

- `batch_workflow.sh` — end-to-end pipeline entry point.
- `terminology_workflow.sh` — applies the human-reviewed terminology decisions
  after the batch pipeline (`run-<tag>/50-terminology/`).
- `prompts/translate.txt` — system prompt used for translation.
- `prompts/terminology-review.txt` — system prompt used for terminology review.
- `schemas/translate.schema.json` — structured output schema for translation.
- `schemas/terminology_review.schema.json` — structured output schema for
  terminology revisions.
- `axters-mistral/` — Axters scholastic lexicon and its CLTK lemmatization.
