#!/usr/bin/env bash
#
# batch_workflow.sh - run the three batch stages in order:
#
#   1. batch_input.py               -> run-<tag>/10_input
#   2. batch_reference_sentences.py -> run-<tag>/20_references
#   3. batch_translate.py           -> run-<tag>/30_translation
#
# Usage:
#   batch_workflow.sh <tsv-file> <start-id> <end-id> <tag> [options]
#
# Options (optional, defaults as in the Python scripts):
#   --batch-size N
#   --reference-number N
#   --openai-model MODEL
#   --reasoning-effort EFFORT
#   --system-prompt FILE
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
TRANSLATE_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --batch-size)       BATCH_SIZE="$2"; shift 2 ;;
        --reference-number) REFERENCE_NUMBER="$2"; shift 2 ;;
        --openai-model|--reasoning-effort|--system-prompt|--api-key)
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

# 3. Translation for every reference batch.
for f in "$RUN_DIR"/20_references/*.json; do
    "$PY" batch_translate.py "$f" "${TRANSLATE_ARGS[@]}"
done
