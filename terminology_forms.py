#!/usr/bin/env python3
"""Enumerate all Latin surface forms of a glossary lemma in a sentence.

Stage 3 stores one glossary entry per ``(sentence, CLTK lemma)`` and records
only the first surface form.  For a form-aware terminology review we need every
occurrence.  This module re-lemmatizes a sentence with the same CLTK lemmatizer
and word pattern as ``batch_glossary.py`` and keeps the tokens whose CLTK lemma
equals the lemma recorded in the run's glossary entry.

Anchoring on the recorded lemma (instead of re-resolving it against the current
lexicon) keeps old runs working when the lexicon or the CLTK model has changed
since the run was produced.
"""

from __future__ import annotations

from dataclasses import dataclass

from cltk.lemmatize.lat import LatinBackoffLemmatizer

from batch_glossary import WORD_PATTERN, normalize_key


@dataclass(frozen=True)
class TokenForm:
    """One occurrence of a lemma-bearing token in a Latin sentence."""

    surface: str
    position: int
    lemma: str


class LatinFormFinder:
    """Find every form of a glossary lemma in a Latin sentence."""

    def __init__(self) -> None:
        self.lemmatizer = LatinBackoffLemmatizer()
        self._lemma_cache: dict[str, str] = {}

    def tokens(self, sentence: str) -> list[TokenForm]:
        """Lemmatize a sentence and return its tokens with their CLTK lemma."""
        tokens = WORD_PATTERN.findall(sentence)
        lowered = [token.lower() for token in tokens]
        unknown = sorted({token for token in lowered if token not in self._lemma_cache})
        if unknown:
            for token, lemma in self.lemmatizer.lemmatize(unknown):
                self._lemma_cache[token] = lemma
        return [
            TokenForm(
                surface=surface,
                position=position,
                lemma=self._lemma_cache.get(lowered_token, lowered_token),
            )
            for position, (surface, lowered_token) in enumerate(zip(tokens, lowered))
        ]

    def occurrences(self, sentence: str, entry: dict) -> list[TokenForm]:
        """Return every token of the sentence whose lemma is the entry's lemma.

        Callers must ensure that a sentence has at most one glossary entry per
        lemma (``terminology_plan.py`` validates this), otherwise tokens cannot
        be attributed unambiguously.
        """
        lemma_key = normalize_key(entry.get("lemma", ""))
        if not lemma_key:
            return []
        matched = [
            token for token in self.tokens(sentence)
            if normalize_key(token.lemma) == lemma_key
        ]
        if not matched and entry.get("source"):
            # Lemma drift safety net: keep the recorded surface form so the
            # sentence is still reviewed.
            matched.append(TokenForm(
                surface=str(entry["source"]), position=-1, lemma=entry.get("lemma", "")
            ))
        return matched

    def surfaces(self, sentence: str, entry: dict) -> list[str]:
        """Return the distinct forms of ``entry`` in sentence order."""
        seen: set[str] = set()
        forms: list[str] = []
        for token in self.occurrences(sentence, entry):
            key = token.surface.lower()
            if key not in seen:
                seen.add(key)
                forms.append(token.surface)
        return forms
