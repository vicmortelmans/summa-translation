#!/usr/bin/env python3
import json
import argparse
import os
import sys
import shlex
from pathlib import Path
from openai import OpenAI
from pydantic import BaseModel, ConfigDict
from typing import Any

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
# Configuration
# ============================================================================

OPENAI_API_KEY_FILE = ".openai_api_key"  # fallback if not provided on commmand line
OPENAI_MODEL = "gpt-5.6-luna"  # fallback if not provided on command line


def _load_openai_api_key() -> str:
    env_key = os.environ.get("OPENAI_API_KEY")
    if env_key:
        return env_key.strip()

    key_path = Path(__file__).resolve().parent / OPENAI_API_KEY_FILE
    if key_path.exists():
        key = key_path.read_text(encoding="utf-8").strip()
        if key:
            return key

    raise RuntimeError(
        "OpenAI API key not found. Please add it to the file '"
        f"{OPENAI_API_KEY_FILE}' in the repository root or set the OPENAI_API_KEY "
        "environment variable."
    )


def write_tokens_output(output_path: str, tokens: dict) -> Path:
    """Write a text file containing the token usage counts."""
    output_path.write_text(
        json.dumps(tokens, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def build_batch_error_command(batch_start: int, batch_end: int) -> str:
    """Return the original invocation with the batch start/end line numbers substituted."""
    command_parts = [sys.executable, *sys.argv]
    for i, part in enumerate(command_parts):
        if part == "--start-id" and i + 1 < len(command_parts):
            command_parts[i + 1] = str(batch_start)
        elif part == "--end-id" and i + 1 < len(command_parts):
            command_parts[i + 1] = str(batch_end)
    return shlex.join(command_parts)


# ============================================================================
# Structured output schema
# ============================================================================

class Translation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    translation: str


class TranslationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    translations: list[Translation]


# ============================================================================
# Main translation function
# ============================================================================

def translate_sentences(
    sentences: list[dict[str, Any]],
    *,
    openai_model: str,
    reasoning_effort: str = "none",
    openai_client: OpenAI,
    system_prompt: str,
) -> list[dict[str, str]]:
    """
    Translate a list of Latin sentences into Dutch.

    Parameters
    ----------
    sentences:
        List of dictionaries:

        {
            "id": "sentence-id",
            "source": "Latin sentence",
            "references": [
                {
                    "source": "similar Latin sentence",
                    "translation": "existing Dutch translation"
                },
                ...
            ]
        }

    Returns
    -------
    List of:

        {
            "id": "sentence-id",
            "translation": "Dutch translation"
        }

    Raises
    ------
    ValueError
        If the returned IDs don't exactly match the input IDs.
    """

    if not sentences:
        return []

    user_message = json.dumps(
        {"sentences": sentences},
        ensure_ascii=False,
        separators=(",", ":"),
    )

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]

    expected_ids = [str(sentence["id"]) for sentence in sentences]

    if reasoning_effort == "none":
        response = openai_client.responses.parse(
            model=openai_model,
            input=messages,
            text_format=TranslationResult,
            reasoning={"effort": reasoning_effort},
            temperature=0,
            top_p=1,
            text={"verbosity": "low"},
            max_output_tokens=128_000,
        )
    else:  # temperature and top_p raise error in gpt-5.6-* when reasoning is enabled, so we omit them.
        response = openai_client.responses.parse(
            model=openai_model,
            input=messages,
            text_format=TranslationResult,
            reasoning={"effort": reasoning_effort},
            text={"verbosity": "low"},
            max_output_tokens=128_000,
        )

    if response.output_parsed is None:
        raise RuntimeError(
            "OpenAI returned no parsed structured output."
        )

    result = response.output_parsed

    tokens = {
        "input": response.usage.input_tokens,
        "cached": response.usage.input_tokens_details.cached_tokens,
        "output": response.usage.output_tokens,
        "reasoning": response.usage.output_tokens_details.reasoning_tokens,
    }

    # ------------------------------------------------------------------------
    # Validate the result.
    # ------------------------------------------------------------------------

    actual_ids = [
        str(item.id)
        for item in result.translations
    ]

    if actual_ids != expected_ids:
        # Log helpful debug information from the response when available.
        try:
            status = getattr(response, "status", None)
            incomplete = getattr(response, "incomplete_details", None)
            usage = getattr(response, "usage", None)
        except Exception:
            status = incomplete = usage = None

        logger.error(
            "Translation ID mismatch. Expected=%s Received=%s",
            expected_ids,
            actual_ids,
        )

        if status is not None:
            logger.error("Response status: %s", status)
        if incomplete is not None:
            logger.error("Response incomplete_details: %s", incomplete)
        if usage is not None:
            logger.error("Response usage: %s", usage)

        raise ValueError(
            "Translation result does not exactly match input IDs.\n"
            f"Expected: {expected_ids}\n"
            f"Received: {actual_ids}\n"
            f"Response status: {status}\n"
            f"Response incomplete_details: {incomplete}\n"
            f"Response usage: {usage}"
        )

    return tokens, [
        {
            "id": item.id,
            "translation": item.translation,
        }
        for item in result.translations
    ]

def main() -> None:
    parser = argparse.ArgumentParser(description="Translate a batch of Latin sentences into Dutch using OpenAI's API.")
    parser.add_argument("filepath", type=str, help="Path to json file used as input for AI translation")
    parser.add_argument("--openai-model", type=str, default=OPENAI_MODEL, help=f"OpenAI model (default: {OPENAI_MODEL})")
    parser.add_argument("--reasoning-effort", type=str, default="none", help=f"Reasoning effort (default: none)")
    parser.add_argument("--api-key", type=str, help=f"OpenAI API key (default: from environment or file)")
    parser.add_argument("--system-prompt", type=str, help=f"System prompt for the AI model")
    args = parser.parse_args()
    run_dir = Path(args.filepath).resolve().parent.parent / "30_translation"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {run_dir}")
    # Read the system prompt from a file if provided, otherwise use a default prompt.
    if args.system_prompt:
        system_prompt_path = Path(args.system_prompt)
        if not system_prompt_path.exists():
            print(f"System prompt file '{system_prompt_path}' does not exist.", file=sys.stderr)
            sys.exit(1)
        system_prompt = system_prompt_path.read_text(encoding="utf-8")
    else:
        system_prompt = (
            "You are a professional translator from Latin to Dutch. "
            "Translate the provided Latin sentences into Dutch, preserving the meaning and context. "
            "Use the reference translations to guide your translation when applicable."
        )
    # Write the system prompt to a file for reference
    (run_dir / "system_prompt.txt").write_text(system_prompt, encoding="utf-8")
    # Write the command line used to start the script
    cmd_line = ' '.join([shlex.quote(arg) for arg in sys.argv])
    command_file = run_dir / "batch_translate_command.txt"
    with command_file.open("a", encoding="utf-8") as handle:
        handle.write(cmd_line + "\n")

    # Create client
    if not args.api_key:
        api_key = _load_openai_api_key()
    else:
        api_key = args.api_key
    openai_client = OpenAI(
        api_key=api_key,
    )

    # Read the input JSON file and add translations for each sentence
    with open(args.filepath, "r", encoding="utf-8") as f:
        payload = json.load(f)

    try:
        tokens, translations = translate_sentences(
            payload,
            openai_model=args.openai_model,
            reasoning_effort=args.reasoning_effort,
            openai_client=openai_client,
            system_prompt=system_prompt,
        )
    except ValueError:
        try:
            tokens, translations = translate_sentences(
                payload,
                openai_model=args.openai_model,
                reasoning_effort=args.reasoning_effort,
                openai_client=openai_client,
                system_prompt=system_prompt,
            )
        except ValueError:
            error_file = run_dir / "batch_translate_ors.txt"
            with error_file.open("a", encoding="utf-8") as handle:
                handle.write(f"{cmd_line}\n")
            print(
                f"Retry failed for {args.filepath} ({run_dir}) "
                f"logged command to {error_file}",
                file=sys.stderr,
            )
            exit(1)
    # Merge the original payload with the translations by id, preserving all
    # original fields (source, references, ...) and appending the translation.
    translations_by_id = {
        str(item["id"]): item["translation"]
        for item in translations
    }
    translations = [
        {**sentence, "translation": translations_by_id[str(sentence["id"])]}
        for sentence in payload
    ]

    # Write the translations to a JSON file in the run directory, preserving the original filename.
    json_path = run_dir / Path(args.filepath).name
    json_path.write_text(json.dumps(translations, ensure_ascii=False, indent=2), encoding="utf-8")

    token_dir = Path(args.filepath).resolve().parent.parent / "31_tokens"
    token_dir.mkdir(parents=True, exist_ok=True)
    token_output_path = token_dir / Path(args.filepath).name

    write_tokens_output(token_output_path, tokens)

    print(f"Processed {len(payload)} sentences")
    print(f"JSON output written to: {json_path}")


if __name__ == "__main__":
    main()

