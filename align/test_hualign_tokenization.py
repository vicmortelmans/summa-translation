import re
from pathlib import Path
import nltk
from nltk.tokenize import PunktSentenceTokenizer

# Download the Punkt data if necessary.
try:
    nltk.data.find("tokenizers/punkt")
except LookupError:
    nltk.download("punkt")


base_dir = Path(__file__).resolve().parent
training_text = (base_dir / "nl_plain.txt").read_text(encoding="utf-8")


def strip_periods_in_bracketed_references(text):
    def remove_periods(match):
        return match.group(0).replace(".", "")

    return re.sub(r"\([^)]*\)|\[[^\]]*\]", remove_periods, text)


def tokenize_text(text):
    # Remove periods inside bracketed references so they do not confuse sentence detection.
    text = strip_periods_in_bracketed_references(text)

    # Split text into sentences using Punkt trained on the Dutch corpus.
    sentence_tokenizer = PunktSentenceTokenizer(training_text)
    sentences = sentence_tokenizer.tokenize(text)

    tokenized_sentences = []

    for sentence in sentences:
        # Separate common punctuation from words.
        sentence = re.sub(r'([.,!?;:()\[\]{}])', r' \1 ', sentence)

        # Normalize whitespace.
        sentence = re.sub(r'\s+', ' ', sentence).strip()

        tokenized_sentences.append(sentence)

    # Two newlines make sentence boundaries visually obvious.
    return "\n\n".join(tokenized_sentences)


print("Enter text. Finish with an empty line:")
print()

lines = []

while True:
    line = input()
    if not line:
        break
    lines.append(line)

text = "\n".join(lines)

print("\n--- Tokenized text ---\n")
print(tokenize_text(text))