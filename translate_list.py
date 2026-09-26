import json
import logging
import os
from typing import Any
from pathlib import Path
from openai import OpenAI

from pydantic import BaseModel, ConfigDict


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


# ============================================================================
# Main translation function
# ============================================================================

def translate_sentences(
    sentences: list[dict[str, Any]],
    *,
    openai_model: str,
    reasoning_effort: str = "none",
    openai_client: OpenAI,
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
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]

    expected_ids = [str(sentence["id"]) for sentence in sentences]

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