#!/usr/bin/env bash
#
# batch_workflow.sh - run the batch stages in order:
#
#   1. batch_input.py               -> run-<tag>/10_input
#   2. batch_reference_sentences.py -> run-<tag>/20_references
#   3. batch_glossary.py            -> run-<tag>/25_glossary
#   4. batch_translate.py           -> run-<tag>/30_translation (+ tokens/)
#   5. batch_merge.py               -> run-<tag>/40-merge
#   6. glossary_application_inventory.py -> run-<tag>/glossary-application-inventory.csv
#      analyse_glossary_consistency.py   -> run-<tag>/glossary-consistency/
#   7. terminology_form_inventory.py    -> run-<tag>/glossary-consistency/lemma_form_inventory.csv
#
# Stage 7 lists every Latin form of the candidate lemmas, so the reviewed
# inter-batch-lemmata-disambiguation.csv can carry form-specific renderings.
# After the review, terminology_workflow.sh <tag> applies the decisions.
#
# Usage:
#   batch_workflow.sh <tsv-file> <start-id> <end-id> <tag> [options]
#
# Options (optional, defaults as in the Python scripts):
#   --batch-size N
#   --reference-number N
#   --openai-model MODEL
#   --reasoning-effort EFFORT
#   --system-prompt FILE            (default: prompts/translate.txt)
#   --api-key KEY
#
set -euo pipefail

cd "$(dirname "$0")"

if [[ -x env/bin/python ]]; then PY=env/bin/python; else PY=python3; fi

TSV="$1"
START_ID="$2"
END_ID="$3"
TAG="$4"
shift 4

BATCH_SIZE=500
REFERENCE_NUMBER=5
SYSTEM_PROMPT="prompts/translate.txt"
SCHEMA="schemas/translate.schema.json"
TRANSLATE_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --batch-size)       BATCH_SIZE="$2"; shift 2 ;;
        --reference-number) REFERENCE_NUMBER="$2"; shift 2 ;;
        --system-prompt)    SYSTEM_PROMPT="$2"; shift 2 ;;
        --openai-model|--reasoning-effort|--api-key)
            TRANSLATE_ARGS+=("$1" "$2"); shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

RUN_DIR="run-$TAG"

# 1. Input batches.
"$PY" batch_input.py "$TSV" --start-id "$START_ID" --end-id "$END_ID" \
    --batch-size "$BATCH_SIZE" --tag "$TAG"

# 2. Reference sentences for every input batch.
for f in "$RUN_DIR"/10_input/*.json; do
    "$PY" batch_reference_sentences.py "$f" --reference-number "$REFERENCE_NUMBER"
done

# 3. Glossary entries for every reference batch.
for f in "$RUN_DIR"/20_references/*.json; do
    "$PY" batch_glossary.py "$f"
done

# 4. Structured LLM output for every glossary batch.
#    Tokens are written to a "tokens" subdirectory of the output directory.
for f in "$RUN_DIR"/25_glossary/*.json; do
    "$PY" batch_translate.py "$f" \
        --schema "$SCHEMA" \
        --output-dir "$RUN_DIR/30_translation" \
        --system-prompt "$SYSTEM_PROMPT" \
        "${TRANSLATE_ARGS[@]}"
done

# 5. Merge all translation batches and accumulate token usage.
"$PY" batch_merge.py --tag "$TAG"

# 6. Glossary application inventory and consistency analysis.
#    The inventory CSV is used by the consistency report (report.md/report.html).
"$PY" glossary_application_inventory.py "$RUN_DIR"
"$PY" analysis-glossary-consistency/scripts/analyse_glossary_consistency.py "$RUN_DIR"

# 7. Per-form reference for the terminology review.
"$PY" terminology_form_inventory.py "$RUN_DIR"
