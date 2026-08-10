#!/usr/bin/env python3
"""AI utility module for local and online model access.

This module exposes `get_responses(prompts, ai='online')` for import from
other Python scripts in this directory.

It supports two AI backends:
1. online AI via OpenAI API
2. local AI via vLLM

When run as a script, prompts are accepted as positional arguments and
responses are printed to stdout separated by a line containing "---".
"""

import argparse
import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import List


OPENAI_API_BASE = "https://api.openai.com/v1"
OPENAI_API_KEY_FILE = ".openai_api_key"
OPENAI_MODEL = "gpt-5.6-luna"
VLLM_MODEL_PATH = "unsloth/gemma-3-27b-it-bnb-4bit"
TOKEN_LOG_FILE = "tokens.csv"
TIME_LOG_FILE = "time.csv"


def get_responses(prompts: List[str], ai: str = "online") -> List[str]:
    """Return model responses for a list of prompts.

    Args:
        prompts: List of prompt strings.
        ai: AI backend to use, one of "online"/"openai" or "local"/"vllm".

    Returns:
        List of response strings in the same order as prompts.
    """
    if not isinstance(prompts, list):
        raise TypeError("prompts must be a list of strings")
    if any(not isinstance(p, str) for p in prompts):
        raise TypeError("each prompt must be a string")

    key = ai.lower()
    if key in {"online", "openai"}:
        return _get_online_responses(prompts)
    if key in {"local", "vllm"}:
        return _get_local_responses(prompts)

    raise ValueError(
        f"Unsupported ai selection: {ai}. Use 'online' or 'local'."
    )


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


def _get_online_responses(prompts: List[str]) -> List[str]:
    try:
        import openai
    except ImportError as exc:
        raise ImportError(
            "openai is not installed in the current environment. "
            "Install it with `pip install openai`."
        ) from exc

    api_key = _load_openai_api_key()

    try:
        client = openai.OpenAI(api_key=api_key, base_url=OPENAI_API_BASE)
    except TypeError:
        openai.api_key = api_key
        openai.api_base = OPENAI_API_BASE
        client = openai

    responses = []
    total_tokens_used = 0
    for prompt in prompts:
        if hasattr(client, "chat"):
            completion = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": prompt},
                ],
                max_completion_tokens=10240,
            )
            max_tokens_used = 10240
        else:
            completion = client.ChatCompletion.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=10240,
            )
            max_tokens_used = 10240

        total_tokens_used += _get_usage_token_count(getattr(completion, "usage", None))

        if _is_truncated(completion, max_tokens_used):
            raise RuntimeError(
                "AI response appears truncated: total_tokens reached the configured max tokens. "
                "Please increase the max token limit or simplify the prompt."
            )

        message = completion.choices[0].message
        if isinstance(message, dict):
            content = message.get("content", "").strip()
        else:
            content = getattr(message, "content", "").strip()
        responses.append(content)

    if prompts:
        _log_token_usage(prompts, OPENAI_MODEL, total_tokens_used)

    return responses


def _get_usage_token_count(usage) -> int:
    if usage is None:
        return 0

    if isinstance(usage, dict):
        total_tokens = usage.get("total_tokens")
        if total_tokens is not None:
            return int(total_tokens)

        prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
        completion_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        completion_details = usage.get("completion_tokens_details")
        if completion_details is None:
            completion_details = usage.get("output_tokens_details")
        reasoning_tokens = 0
        if isinstance(completion_details, dict):
            reasoning_tokens = completion_details.get("reasoning_tokens", 0) or 0
        elif completion_details is not None:
            reasoning_tokens = getattr(completion_details, "reasoning_tokens", 0) or 0
        return int(prompt_tokens) + int(completion_tokens) + int(reasoning_tokens)

    total_tokens = getattr(usage, "total_tokens", None)
    if total_tokens is not None:
        return int(total_tokens)

    prompt_tokens = getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", 0)) or 0
    completion_tokens = getattr(usage, "completion_tokens", getattr(usage, "output_tokens", 0)) or 0
    completion_details = getattr(usage, "completion_tokens_details", getattr(usage, "output_tokens_details", None))
    reasoning_tokens = 0
    if completion_details is not None:
        reasoning_tokens = getattr(completion_details, "reasoning_tokens", 0) or 0
    return int(prompt_tokens) + int(completion_tokens) + int(reasoning_tokens)


def _log_token_usage(prompts: List[str], model_name: str, total_tokens_used: int) -> None:
    log_path = Path(__file__).resolve().parent / TOKEN_LOG_FILE
    write_header = not log_path.exists() or log_path.stat().st_size == 0

    with log_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if write_header:
            writer.writerow(["timestamp", "model", "prompt_count", "total_tokens"])
        writer.writerow([
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            model_name,
            len(prompts),
            total_tokens_used,
        ])


def _is_truncated(completion, max_tokens_used: int) -> bool:
    usage = getattr(completion, "usage", None)
    if usage is None:
        usage = getattr(completion, "usage", {})

    total_tokens = None
    if isinstance(usage, dict):
        total_tokens = usage.get("total_tokens")
    else:
        total_tokens = getattr(usage, "total_tokens", None)

    if total_tokens is not None and total_tokens >= max_tokens_used:
        return True

    try:
        finish_reason = completion.choices[0].finish_reason
    except Exception:
        finish_reason = None

    if finish_reason == "length":
        return True

    return False


def _get_local_responses(prompts: List[str]) -> List[str]:
    try:
        from vllm import LLM, SamplingParams
    except ImportError as exc:
        raise ImportError(
            "vllm is not installed in the current environment. "
            "Install it with `pip install vllm`."
        ) from exc

    if not VLLM_MODEL_PATH:
        raise RuntimeError(
            "VLLM_MODEL_PATH is not configured. Please set a valid model path in ai.py."
        )

    llm = LLM(model=VLLM_MODEL_PATH)
    sampling_params = SamplingParams(temperature=0.7, top_p=0.95, max_tokens=10240)
    responses = []

    start = perf_counter()
    for generation in llm.generate(
        prompts,
        sampling_params=sampling_params,
    ):
        if not generation.outputs:
            responses.append("")
            continue
        responses.append(generation.outputs[0].text.strip())
    elapsed = perf_counter() - start

    if prompts:
        _log_time_usage(prompts, VLLM_MODEL_PATH, elapsed)

    return responses


def _log_time_usage(prompts: List[str], model_name: str, seconds: float) -> None:
    log_path = Path(__file__).resolve().parent / TIME_LOG_FILE
    write_header = not log_path.exists() or log_path.stat().st_size == 0

    with log_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if write_header:
            writer.writerow(["timestamp", "model", "prompt_count", "duration_seconds"])
        writer.writerow([
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            model_name,
            len(prompts),
            round(seconds, 6),
        ])


def _print_responses(responses: List[str]) -> None:
    for index, response in enumerate(responses):
        if index > 0:
            print("---")
        print(response)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run AI prompt responses from the command line."
    )
    parser.add_argument(
        "-a",
        "--ai",
        choices=["online", "openai", "local", "vllm"],
        default="online",
        help="AI backend to use (default: online)",
    )
    parser.add_argument(
        "prompts",
        nargs="+",
        help="Prompts to send to the AI backend",
    )
    args = parser.parse_args()

    responses = get_responses(args.prompts, ai=args.ai)
    _print_responses(responses)


if __name__ == "__main__":
    main()

