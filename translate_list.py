import json
import logging
import os
from typing import Any
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel, ConfigDict


# ============================================================================
# Configuration
# ============================================================================

OPENAI_API_KEY_FILE = ".openai_api_key"
OPENAI_MODEL = "gpt-5.6-luna"

VLLM_MODEL = "Qwen/Qwen3-30B-A3B-Instruct-2507"
VLLM_BASE_URL = "http://localhost:8000/v1"
# Running the server: 
#   vllm serve Qwen/Qwen3-30B-A3B-Instruct-2507 --max-model-len 32768 --performance-mode throughput


# ============================================================================
# Logging
# ============================================================================

logger = logging.getLogger(__name__)


# ============================================================================
# Translation instructions
# ============================================================================

SYSTEM_PROMPT = """\
You are a professional translator specializing in Latin-to-Dutch translation.

Translate every source sentence in the input into accurate, natural Dutch.

Each source sentence is accompanied by reference examples consisting of similar
Latin sentences and their existing Dutch translations. Use these references as
guidance, especially for consistent translation of recurring words, phrases,
terminology, names, and expressions.

The references are examples, not authoritative translations. Use them when they
are relevant, but do not blindly copy their wording. Always determine the
meaning of the current Latin sentence from its own grammatical and semantic
context.

Preserve the meaning and distinctions of the Latin, including negation, tense,
modality, qualifications, and grammatical relationships. Do not add information
that is not present in the source. Prefer natural Dutch while maintaining the
appropriate style and register.

Translate every source sentence exactly once. Preserve each sentence's ID
exactly. Do not translate or reproduce the reference sentences. Do not provide
explanations, alternatives, notes, or commentary.
"""


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


# This same schema is used by vLLM.
TRANSLATION_SCHEMA = TranslationResult.model_json_schema()


# ============================================================================
# Clients
# ============================================================================

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


api_key = _load_openai_api_key()
openai_client = OpenAI(
    api_key=api_key,
)

vllm_client = OpenAI(
    base_url=VLLM_BASE_URL,
    # vLLM doesn't need a real API key unless you configured one.
    api_key="EMPTY",
)


# ============================================================================
# Main translation function
# ============================================================================

def translate_sentences(
    sentences: list[dict[str, Any]],
    *,
    online: bool,
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

    online:
        True  -> OpenAI
        False -> local vLLM

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

    # ------------------------------------------------------------------------
    # Prepare the common messages.
    #
    # These are IDENTICAL for OpenAI and vLLM.
    # ------------------------------------------------------------------------

    user_message = json.dumps(
        {"sentences": sentences},
        ensure_ascii=False,
        separators=(",", ":"),
    )

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]

    expected_ids = [str(sentence["id"]) for sentence in sentences]

    # ------------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------------

    if online:

        response = openai_client.responses.parse(
            model=OPENAI_MODEL,
            input=messages,
            text_format=TranslationResult,
        )

        if response.output_parsed is None:
            raise RuntimeError(
                "OpenAI returned no parsed structured output."
            )

        result = response.output_parsed

        # Token usage
        if response.usage is not None:
            reasoning_tokens = getattr(response.usage, "reasoning_tokens", 0) or 0
            if reasoning_tokens > 0:
                logger.info(
                    "OpenAI tokens: input=%s output=%s reasoning=%s total=%s",
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                    reasoning_tokens,
                    response.usage.total_tokens,
                )
            else:
                logger.info(
                    "OpenAI tokens: input=%s output=%s total=%s (no reasoning)",
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                    response.usage.total_tokens,
                )

    # ------------------------------------------------------------------------
    # vLLM
    # ------------------------------------------------------------------------

    else:

        response = vllm_client.chat.completions.create(
            model=VLLM_MODEL,
            messages=messages,

            # vLLM structured output.
            #
            # Current vLLM uses "structured_outputs". The "json" value is
            # the JSON Schema generated from our Pydantic model.
            extra_body={
                "structured_outputs": {
                    "json": TRANSLATION_SCHEMA,
                },
            },

            # Translation doesn't need stochastic generation.
            temperature=0.0,
        )

        if not response.choices:
            raise RuntimeError(
                "vLLM returned no choices."
            )

        raw_output = response.choices[0].message.content

        if not raw_output:
            raise RuntimeError(
                "vLLM returned empty output."
            )

        result = TranslationResult.model_validate_json(
            raw_output
        )

        # Token usage
        if response.usage is not None:
            reasoning_tokens = getattr(response.usage, "reasoning_tokens", 0) or 0
            if reasoning_tokens > 0:
                logger.info(
                    "vLLM tokens: input=%s output=%s reasoning=%s total=%s",
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens,
                    reasoning_tokens,
                    response.usage.total_tokens,
                )
            else:
                logger.info(
                    "vLLM tokens: input=%s output=%s total=%s (no reasoning)",
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens,
                    response.usage.total_tokens,
                )

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

    return [
        {
            "id": item.id,
            "translation": item.translation,
        }
        for item in result.translations
    ]