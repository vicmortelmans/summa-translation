#!/usr/bin/env python3
import json
import argparse
import os
import re
import sys
import shlex
from pathlib import Path
from typing import Any

import jsonschema
from openai import OpenAI

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
DEFAULT_INPUT_KEY = "items"  # key under which the input items are sent to the model
MAX_OUTPUT_TOKENS = 128_000
SCRIPT_STEM = Path(__file__).stem


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


def write_tokens_output(output_path: Path, tokens: dict) -> Path:
    """Write a text file containing the token usage counts."""
    output_path.write_text(
        json.dumps(tokens, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


# ============================================================================
# Structured output schema helpers
# ============================================================================

def load_output_schema(schema_path: Path) -> dict[str, Any]:
    """Load the JSON schema that defines the structured model output."""
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Schema file '{schema_path}' does not exist.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"Schema file '{schema_path}' is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(schema, dict):
        print(f"Schema file '{schema_path}' must contain a JSON object.", file=sys.stderr)
        sys.exit(1)
    return schema


def schema_format_name(schema: dict[str, Any], schema_path: Path) -> str:
    """Derive the response format name required by the OpenAI API.

    The name must consist of a-z, A-Z, 0-9, underscores and dashes and may be at
    most 64 characters long.
    """
    raw_name = schema.get("title") or schema_path.stem or "response"
    name = re.sub(r"[^A-Za-z0-9_-]", "_", str(raw_name))[:64]
    return name or "response"


def check_strict_schema_compatibility(schema: Any, path: str = "$") -> None:
    """Verify that a schema can be used with OpenAI's strict structured outputs.

    Strict mode requires every object to set "additionalProperties": false and to
    list all of its properties in "required".
    """
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            if schema.get("additionalProperties") is not False:
                print(
                    f'Schema at {path} must set "additionalProperties": false to be used '
                    "with OpenAI strict structured outputs (or pass --no-strict).",
                    file=sys.stderr,
                )
                sys.exit(1)

            required = schema.get("required", [])
            if not isinstance(required, list):
                required = []
            missing = sorted(set(properties) - set(required))
            if missing:
                print(
                    f'Schema at {path} must list every property in "required" to be used '
                    f"with OpenAI strict structured outputs; missing: {', '.join(missing)} "
                    "(or pass --no-strict).",
                    file=sys.stderr,
                )
                sys.exit(1)

        for key, value in schema.items():
            check_strict_schema_compatibility(value, f"{path}.{key}")
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            check_strict_schema_compatibility(value, f"{path}[{index}]")


def resolve_output_records(
    output: Any,
    output_list_key: str | None = None,
) -> list[Any]:
    """Return the list of result records from the parsed model output.

    The output is expected to be either a JSON array of records or an object with
    exactly one array-valued field (e.g. {"translations": [...]}). Use
    `output_list_key` to point at the field explicitly when the output contains
    more than one array.
    """
    if output_list_key:
        if not isinstance(output, dict) or output_list_key not in output:
            raise ValueError(
                f"Output list key '{output_list_key}' not found in the model output."
            )
        records = output[output_list_key]
    elif isinstance(output, list):
        records = output
    elif isinstance(output, dict):
        list_values = [value for value in output.values() if isinstance(value, list)]
        if len(list_values) != 1:
            raise ValueError(
                "Cannot determine the list of result records in the model output: "
                f"found {len(list_values)} list-valued fields. "
                "Pass --output-list-key to select one."
            )
        records = list_values[0]
    else:
        raise ValueError(
            "Model output must be a JSON array or a JSON object, "
            f"got {type(output).__name__}."
        )

    if not isinstance(records, list):
        raise ValueError("The selected model output records must be a JSON array.")
    return records


# ============================================================================
# Main inference function
# ============================================================================

def run_inference(
    items: list[dict[str, Any]],
    *,
    openai_model: str,
    output_schema: dict[str, Any],
    schema_name: str,
    openai_client: OpenAI,
    system_prompt: str,
    reasoning_effort: str = "none",
    input_key: str = DEFAULT_INPUT_KEY,
    output_list_key: str | None = None,
    strict: bool = True,
) -> tuple[dict[str, int], Any]:
    """
    Run a structured LLM inference on a list of input items.

    Parameters
    ----------
    items:
        List of dictionaries that each contain an "id" field. The items are sent
        to the model as-is, as JSON, under the key given by ``input_key``.

    output_schema:
        JSON schema that defines the structure of the model output.

    schema_name:
        Name of the response format, as required by the OpenAI API.

    Returns
    -------
    (tokens, output)
        Token usage counts and the parsed model output (a JSON object or array
        that conforms to ``output_schema``).

    Raises
    ------
    ValueError
        If the output does not conform to the schema, or if the returned IDs don't
        exactly match the input IDs.
    """

    if not items:
        raise ValueError("No input items to process.")

    expected_ids = [str(item["id"]) for item in items]

    user_message = json.dumps(
        {input_key: items},
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

    request: dict[str, Any] = {
        "model": openai_model,
        "input": messages,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": output_schema,
                "strict": strict,
            },
            "verbosity": "low",
        },
        "reasoning": {"effort": reasoning_effort},
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }

    if reasoning_effort == "none":
        request["temperature"] = 0
        request["top_p"] = 1
    # temperature and top_p raise an error in gpt-5.6-* when reasoning is enabled,
    # so they are omitted in that case.

    response = openai_client.responses.create(**request)

    raw_output = response.output_text
    if not raw_output:
        raise RuntimeError(
            "OpenAI returned no structured output."
        )

    try:
        output = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OpenAI returned output that is not valid JSON: {exc}") from exc

    try:
        jsonschema.validate(instance=output, schema=output_schema)
    except jsonschema.ValidationError as exc:
        raise ValueError(
            "OpenAI output does not conform to the output schema: "
            f"{exc.message}"
        ) from exc

    tokens = {
        "input": response.usage.input_tokens,
        "cached": response.usage.input_tokens_details.cached_tokens,
        "output": response.usage.output_tokens,
        "reasoning": response.usage.output_tokens_details.reasoning_tokens,
    }

    # ------------------------------------------------------------------------
    # Validate completeness: every input item must be processed exactly once.
    # ------------------------------------------------------------------------

    output_records = resolve_output_records(output, output_list_key)

    actual_ids = []
    for index, record in enumerate(output_records):
        if not isinstance(record, dict) or "id" not in record:
            raise ValueError(
                f"Output record {index} does not contain an 'id' field, so "
                "completeness cannot be verified."
            )
        actual_ids.append(str(record["id"]))

    if actual_ids != expected_ids:
        # Log helpful debug information from the response when available.
        try:
            status = getattr(response, "status", None)
            incomplete = getattr(response, "incomplete_details", None)
            usage = getattr(response, "usage", None)
        except Exception:
            status = incomplete = usage = None

        logger.error(
            "Output ID mismatch. Expected=%s Received=%s",
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
            "Model output does not exactly match the input IDs.\n"
            f"Expected: {expected_ids}\n"
            f"Received: {actual_ids}\n"
            f"Response status: {status}\n"
            f"Response incomplete_details: {incomplete}\n"
            f"Response usage: {usage}"
        )

    return tokens, output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a structured LLM inference (JSON input, JSON schema output) using OpenAI's API."
    )
    parser.add_argument("filepath", type=str, help="Path to the input JSON file: a list of objects that each contain an 'id' field")
    parser.add_argument("--output-dir", type=str, required=True, help="Directory for the model output, token usage and logging files")
    parser.add_argument("--schema", type=str, required=True, help="Path to the JSON schema that defines the model output")
    parser.add_argument("--system-prompt", type=str, required=True, help="Path to a text file containing the system prompt for the AI model")
    parser.add_argument("--input-key", type=str, default=DEFAULT_INPUT_KEY, help=f"Key under which the input items are sent to the model (default: {DEFAULT_INPUT_KEY})")
    parser.add_argument("--output-list-key", type=str, help="Key of the output field that contains the result records (default: the only list-valued field)")
    parser.add_argument("--openai-model", type=str, default=OPENAI_MODEL, help=f"OpenAI model (default: {OPENAI_MODEL})")
    parser.add_argument("--reasoning-effort", type=str, default="none", help=f"Reasoning effort (default: none)")
    parser.add_argument("--api-key", type=str, help=f"OpenAI API key (default: from environment or file)")
    parser.add_argument("--no-strict", action="store_true", help="Do not enforce the schema with OpenAI's strict structured outputs")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {output_dir}")

    # Read the JSON schema that defines the structured model output.
    schema_path = Path(args.schema)
    output_schema = load_output_schema(schema_path)
    schema_name = schema_format_name(output_schema, schema_path)
    if not args.no_strict:
        check_strict_schema_compatibility(output_schema)

    # Read the system prompt from a file.
    system_prompt_path = Path(args.system_prompt)
    if not system_prompt_path.exists():
        print(f"System prompt file '{system_prompt_path}' does not exist.", file=sys.stderr)
        sys.exit(1)
    system_prompt = system_prompt_path.read_text(encoding="utf-8")

    # Write the system prompt to a file for reference
    (output_dir / "system_prompt.txt").write_text(system_prompt, encoding="utf-8")
    # Write the command line used to start the script
    cmd_line = ' '.join([shlex.quote(arg) for arg in sys.argv])
    command_file = output_dir / f"{SCRIPT_STEM}_command.txt"
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

    # Read the input JSON file: a list of objects that each contain an "id".
    with open(args.filepath, "r", encoding="utf-8") as f:
        input_data = json.load(f)

    if not isinstance(input_data, list):
        print(f"Input file '{args.filepath}' must contain a JSON array.", file=sys.stderr)
        sys.exit(1)
    if not input_data:
        print(f"Input file '{args.filepath}' contains no items.", file=sys.stderr)
        sys.exit(1)
    for index, item in enumerate(input_data):
        if not isinstance(item, dict) or "id" not in item:
            print(
                f"Input item {index} in '{args.filepath}' does not contain an 'id' field.",
                file=sys.stderr,
            )
            sys.exit(1)

    inference_kwargs = dict(
        openai_model=args.openai_model,
        reasoning_effort=args.reasoning_effort,
        output_schema=output_schema,
        schema_name=schema_name,
        input_key=args.input_key,
        output_list_key=args.output_list_key,
        strict=not args.no_strict,
        openai_client=openai_client,
        system_prompt=system_prompt,
    )

    for attempt in range(2):
        try:
            tokens, output = run_inference(input_data, **inference_kwargs)
            break
        except ValueError:
            if attempt:
                error_file = output_dir / f"{SCRIPT_STEM}_errors.txt"
                with error_file.open("a", encoding="utf-8") as handle:
                    handle.write(f"{cmd_line}\n")
                print(
                    f"Retry failed for {args.filepath} ({output_dir}) "
                    f"logged command to {error_file}",
                    file=sys.stderr,
                )
                exit(1)
            logger.warning("Inference failed for %s, retrying once.", args.filepath)

    # Write the model output to a JSON file in the output directory, preserving
    # the original filename. The output is saved as-is: it is not merged with the
    # input items.
    json_path = output_dir / Path(args.filepath).name
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    token_dir = output_dir / "tokens"
    token_dir.mkdir(parents=True, exist_ok=True)
    token_output_path = token_dir / Path(args.filepath).name

    write_tokens_output(token_output_path, tokens)

    print(f"Processed {len(input_data)} items")
    print(f"JSON output written to: {json_path}")
    print(f"Token usage written to: {token_output_path}")


if __name__ == "__main__":
    main()

