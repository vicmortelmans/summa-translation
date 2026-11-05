#!/usr/bin/env python3
import json
import argparse
import sys
import shlex
from pathlib import Path
from vector.search import find_similar

import logging

# ============================================================================
# Logging
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================================



def build_translation_input(rows: list[dict[str, str]], reference_number: int = 5) -> list[dict]:
    """Compose the list of dictionaries required by translate_sentences()."""
    payload: list[dict] = []

    for index, row in enumerate(rows, start=1):
        latin = row["source"]
        similar = find_similar(latin, n=reference_number)

        references = [
            {
                "source": item["latin"],
                "translation": item["dutch"],
            }
            for item in similar
        ]

        payload.append(
            {
                "id": str(row["id"]),
                "source": latin,
                "references": references,
            }
        )

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Add reference translations to a batch of sentences")
    parser.add_argument("filepath", type=str, help="Path to input JSON file")
    parser.add_argument("--reference-number", type=int, default=5, help=f"Number of reference translations (default: 5)")
    args = parser.parse_args()
    run_dir = Path(args.filepath).resolve().parent.parent / "20_references"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {run_dir}")

    # Write the command line used to start the script
    cmd_line = ' '.join([shlex.quote(arg) for arg in sys.argv])
    command_file = run_dir / "batch_reference_sentences_command.txt"
    with open(command_file, "a", encoding="utf-8") as f:
        f.write(cmd_line + "\n")

    # Read the input JSON file and add reference translations for each sentence
    with open(args.filepath, "r", encoding="utf-8") as f:
        batch_rows = json.load(f)
    payload = build_translation_input(batch_rows, reference_number=args.reference_number)
    json_path = run_dir / Path(args.filepath).name
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Processed {len(batch_rows)} sentences")
    print(f"JSON output written to: {json_path}")


if __name__ == "__main__":
    main()

