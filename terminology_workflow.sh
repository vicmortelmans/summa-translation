#!/usr/bin/env bash
#
# terminology_workflow.sh - apply the reviewed terminology decisions of a run.
#
# The preliminary workflow (batch_workflow.sh) ends with a reviewed
# glossary-consistency/inter-batch-lemmata-disambiguation.csv.  After a human
# has filled in the preferred renderings, this script harmonises the
# translation:
#
#   1. terminology_plan.py          -> run-<tag>/50-terminology/10_plan
#   2. batch_translate.py (per part) -> run-<tag>/50-terminology/20_review/<lemma>
#   3. terminology_apply.py         -> run-<tag>/50-terminology/30_translation
#                                      run-<tag>/50-terminology/40_changes
#   4. batch_merge.py               -> run-<tag>/50-terminology/40-merge
#   5. glossary_application_inventory.py + analyse_glossary_consistency.py
#                                   -> run-<tag>/50-terminology/50_verification
#   6. terminology_verify.py        -> comparison.csv + report.md
#
# Lemmas are applied one by one in review-file order, so later passes see the
# corrections of earlier ones (and keep them locked).
#
# Usage:
#   terminology_workflow.sh <tag> [options]
#
# Options:
#   --review-csv FILE             reviewed CSV (default: .reviewed.csv, else the plain file)
#   --lemma NAME                  only process this lemma (repeatable)
#   --terminology-dir DIR         output root (default: run-<tag>/50-terminology)
#   --sentences-per-request N     sentences per request (default: 50)
#   --examples N                  preferred-translation examples per item (default: 2)
#   --openai-model MODEL          passed to batch_translate.py
#   --reasoning-effort EFFORT     passed to batch_translate.py
#   --api-key KEY                 passed to batch_translate.py
#   --max-change-ratio RATIO      flag larger rewrites (default: 0.5)
#   --dry-run                     only validate the CSV and print plan counts
#   --force                       redo existing requests and revisions
#   --reset                       remove run-<tag>/50-terminology first
#
# Existing revision files are reused unless --force is given.  Re-run with
# --force after editing the reviewed CSV or after changing the base layer, so
# stale model output cannot be applied.
#
set -euo pipefail

cd "$(dirname "$0")"

if [[ -x env/bin/python ]]; then PY=env/bin/python; else PY=python3; fi

TAG="${1:-}"
if [[ -z "$TAG" || "$TAG" == --* ]]; then
    echo "Usage: terminology_workflow.sh <tag> [options]" >&2
    exit 2
fi
shift

REVIEW_CSV=""
TERMINOLOGY_DIR=""
SENTENCES_PER_REQUEST=50
EXAMPLES=2
MAX_CHANGE_RATIO=0.5
DRY_RUN=0
FORCE=0
RESET=0
LEMMAS=()
TRANSLATE_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --review-csv)            REVIEW_CSV="$2"; shift 2 ;;
        --terminology-dir)       TERMINOLOGY_DIR="$2"; shift 2 ;;
        --lemma)                 LEMMAS+=("$2"); shift 2 ;;
        --sentences-per-request) SENTENCES_PER_REQUEST="$2"; shift 2 ;;
        --examples)              EXAMPLES="$2"; shift 2 ;;
        --max-change-ratio)      MAX_CHANGE_RATIO="$2"; shift 2 ;;
        --openai-model|--reasoning-effort|--api-key)
            TRANSLATE_ARGS+=("$1" "$2"); shift 2 ;;
        --dry-run)               DRY_RUN=1; shift ;;
        --force)                 FORCE=1; shift ;;
        --reset)                 RESET=1; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

RUN_DIR="run-$TAG"
TERM_DIR="${TERMINOLOGY_DIR:-$RUN_DIR/50-terminology}"
PLAN_DIR="$TERM_DIR/10_plan"
REVIEW_DIR="$TERM_DIR/20_review"
OUT_LAYER="$TERM_DIR/30_translation"
CHANGES_DIR="$TERM_DIR/40_changes"
VERIFY_DIR="$TERM_DIR/50_verification"

if [[ ! -d "$RUN_DIR" ]]; then
    echo "Run directory not found: $RUN_DIR" >&2
    exit 1
fi

if [[ -z "$REVIEW_CSV" ]]; then
    if [[ -f "$RUN_DIR/glossary-consistency/inter-batch-lemmata-disambiguation.reviewed.csv" ]]; then
        REVIEW_CSV="$RUN_DIR/glossary-consistency/inter-batch-lemmata-disambiguation.reviewed.csv"
    elif [[ -f "$RUN_DIR/glossary-consistency/inter-batch-lemmata-disambiguation.csv" ]]; then
        REVIEW_CSV="$RUN_DIR/glossary-consistency/inter-batch-lemmata-disambiguation.csv"
    else
        echo "No reviewed CSV found in $RUN_DIR/glossary-consistency" >&2
        exit 1
    fi
fi

if [[ "$RESET" -eq 1 ]]; then
    echo "Resetting $TERM_DIR"
    rm -rf "$TERM_DIR"
fi

mkdir -p "$PLAN_DIR" "$REVIEW_DIR" "$CHANGES_DIR" "$VERIFY_DIR"

LEMMA_ARGS=()
for lemma in "${LEMMAS[@]:-}"; do
    if [[ -n "$lemma" ]]; then
        LEMMA_ARGS+=(--lemma "$lemma")
    fi
done

# 0. Validate the reviewed CSV and report the plan without writing requests.
"$PY" terminology_plan.py "$RUN_DIR" \
    --review-csv "$REVIEW_CSV" \
    --terminology-dir "$TERM_DIR" \
    --sentences-per-request "$SENTENCES_PER_REQUEST" \
    --examples "$EXAMPLES" \
    --dry-run "${LEMMA_ARGS[@]}"

if [[ "$DRY_RUN" -eq 1 ]]; then
    exit 0
fi

# Lemma order: explicit --lemma list, else the order of the review file.
if [[ ${#LEMMAS[@]} -gt 0 ]]; then
    LEMMA_LIST=("${LEMMAS[@]}")
else
    mapfile -t LEMMA_LIST < <("$PY" - "$REVIEW_CSV" <<'PYEOF'
import sys
from pathlib import Path
from terminology_common import load_review_rows, review_lemmas

print("\n".join(review_lemmas(load_review_rows(Path(sys.argv[1])))))
PYEOF
)
fi

if [[ ${#LEMMA_LIST[@]} -eq 0 ]]; then
    echo "No lemmas with a preferred translation in $REVIEW_CSV" >&2
    exit 1
fi

# Start from the corrected layer when it exists, else from the preliminary one.
if [[ -n "$(find "$OUT_LAYER" -maxdepth 1 -name '*.json' -print -quit 2>/dev/null)" ]]; then
    LAYER="$OUT_LAYER"
else
    LAYER="$RUN_DIR/30_translation"
fi

for lemma in "${LEMMA_LIST[@]}"; do
    echo "== $lemma =="
    "$PY" terminology_plan.py "$RUN_DIR" \
        --review-csv "$REVIEW_CSV" \
        --terminology-dir "$TERM_DIR" \
        --layer "$LAYER" \
        --sentences-per-request "$SENTENCES_PER_REQUEST" \
        --examples "$EXAMPLES" \
        --lemma "$lemma"

    safe_lemma="${lemma//[^[:alnum:]_.-]/_}"
    request_dir="$PLAN_DIR/requests/$safe_lemma"
    if ! compgen -G "$request_dir/part*.json" > /dev/null; then
        echo "No requests for $lemma; skipping."
        continue
    fi

    for request in "$request_dir"/part*.json; do
        revision="$REVIEW_DIR/$safe_lemma/$(basename "$request")"
        if [[ -f "$revision" && "$FORCE" -eq 0 ]]; then
            echo "Keeping existing revision: $revision"
            continue
        fi
        "$PY" batch_translate.py "$request" \
            --schema schemas/terminology_review.schema.json \
            --output-dir "$REVIEW_DIR/$safe_lemma" \
            --system-prompt prompts/terminology-review.txt \
            "${TRANSLATE_ARGS[@]}"
    done

    "$PY" terminology_apply.py "$RUN_DIR" \
        --terminology-dir "$TERM_DIR" \
        --lemma "$lemma" \
        --input-layer "$LAYER" \
        --output-layer "$OUT_LAYER" \
        --changes-dir "$CHANGES_DIR" \
        --max-change-ratio "$MAX_CHANGE_RATIO"

    LAYER="$OUT_LAYER"
done

# Merge the corrected layer (batch_merge.py accepts any job directory).
"$PY" batch_merge.py "$TERM_DIR"

# Verify the corrected layer with the existing stage-6 scripts.
"$PY" glossary_application_inventory.py "$RUN_DIR" \
    --glossary-dir "$RUN_DIR/25_glossary" \
    --translation-dir "$OUT_LAYER" \
    -o "$VERIFY_DIR/glossary-application-inventory.csv"
"$PY" analysis-glossary-consistency/scripts/analyse_glossary_consistency.py "$RUN_DIR" \
    --inventory "$VERIFY_DIR/glossary-application-inventory.csv" \
    --translation-dir "$OUT_LAYER" \
    -o "$VERIFY_DIR/glossary-consistency"
"$PY" terminology_verify.py "$RUN_DIR" \
    --terminology-dir "$TERM_DIR" \
    --review-csv "$REVIEW_CSV"

echo "Terminology harmonisation finished: $TERM_DIR"

