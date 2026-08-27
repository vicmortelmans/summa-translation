from pathlib import Path
import pickle
import re

import pandas as pd
from rank_bm25 import BM25Okapi


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

CSV_FILE = Path("latin_nl_align.txt")
INDEX_FILE = Path("bm25_index.pkl")

# The TSV has no header row and contains: latin, dutch, score.
LATIN_COLUMN = "latin"
ID_COLUMN = "id"


# ---------------------------------------------------------
# Tokenization
# ---------------------------------------------------------

def tokenize_latin(text: str) -> list[str]:
    """
    Convert a Hunalign-tokenized Latin sentence into tokens.

    Since Hunalign has already separated punctuation, we mostly
    just need to split on whitespace.

    Lowercasing makes lexical matching case-insensitive.
    """

    text = str(text).strip().lower()

    tokens = text.split()

    return tokens


# ---------------------------------------------------------
# Load data
# ---------------------------------------------------------

print(f"Loading {CSV_FILE}...")

df = pd.read_csv(CSV_FILE, sep="\t", header=None, names=["latin", "dutch", "score"])
df = df.dropna(subset=["latin", "dutch", "score"])

df.insert(0, ID_COLUMN, range(1, len(df) + 1))

if LATIN_COLUMN not in df.columns:
    raise ValueError(
        f"Column '{LATIN_COLUMN}' not found in TSV."
    )

print(f"Loaded {len(df):,} sentence pairs.")


# ---------------------------------------------------------
# Tokenize
# ---------------------------------------------------------

print("Tokenizing sentences...")

tokenized_sentences = [
    tokenize_latin(sentence)
    for sentence in df[LATIN_COLUMN]
]


# ---------------------------------------------------------
# Build BM25 index
# ---------------------------------------------------------

print("Building BM25 index...")

bm25 = BM25Okapi(tokenized_sentences)


# ---------------------------------------------------------
# Save everything
# ---------------------------------------------------------

INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)

data = {
    "bm25": bm25,
    "ids": df[ID_COLUMN].tolist(),
    "sentences": df[LATIN_COLUMN].tolist(),
    "dutch": df["dutch"].tolist(),
    "score": df["score"].tolist(),
    "records": df[[ID_COLUMN, "latin", "dutch", "score"]].to_dict(orient="records"),
}

with open(INDEX_FILE, "wb") as f:
    pickle.dump(data, f)


print()
print("BM25 index created.")
print(f"Index:     {INDEX_FILE}")
print(f"Sentences: {len(tokenized_sentences):,}")