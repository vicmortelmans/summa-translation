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
from typing import List


OPENAI_API_BASE = "https://api.openai.com/v1"
OPENAI_API_KEY = "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"
OPENAI_MODEL = "gpt-5.6-luna"
VLLM_MODEL_PATH = "/path/to/vllm/model"


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


def _get_online_responses(prompts: List[str]) -> List[str]:
    try:
        import openai
    except ImportError as exc:
        raise ImportError(
            "openai is not installed in the current environment. "
            "Install it with `pip install openai`."
        ) from exc

    if OPENAI_API_KEY in {"", "YOUR_OPENAI_API_KEY"}:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Please replace the placeholder in ai.py "
            "with your OpenAI API key."
        )

    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_API_BASE)
    except TypeError:
        openai.api_key = OPENAI_API_KEY
        openai.api_base = OPENAI_API_BASE
        client = openai

    responses = []
    for prompt in prompts:
        if hasattr(client, "chat"):
            completion = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": prompt},
                ],
                max_completion_tokens=1024,
            )
        else:
            completion = client.ChatCompletion.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1024,
            )

        message = completion.choices[0].message
        if isinstance(message, dict):
            content = message.get("content", "").strip()
        else:
            content = getattr(message, "content", "").strip()
        responses.append(content)

    return responses


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
    sampling_params = SamplingParams(temperature=0.7, top_p=0.95)
    responses = []

    try:
        for generation in llm.generate(
            prompts,
            sampling_params=sampling_params,
            max_tokens=1024,
        ):
            if not generation.outputs:
                responses.append("")
                continue
            responses.append(generation.outputs[0].text.strip())
    finally:
        try:
            llm.close()
        except AttributeError:
            llm.shutdown()

    return responses


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
