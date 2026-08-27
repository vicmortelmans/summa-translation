from pathlib import Path
import pickle

from build_bm25 import tokenize_latin


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

INDEX_FILE = Path("bm25_index.pkl")


# ---------------------------------------------------------
# Load index
# ---------------------------------------------------------

print("Loading BM25 index...")

with open(INDEX_FILE, "rb") as f:
    data = pickle.load(f)

bm25 = data["bm25"]
ids = data["ids"]
sentences = data["sentences"]


# ---------------------------------------------------------
# Search
# ---------------------------------------------------------

def find_similar_lexical(
    latin_sentence: str,
    n: int = 10,
):
    """
    Return the n sentences with the highest BM25 score.
    """

    query_tokens = tokenize_latin(latin_sentence)

    scores = bm25.get_scores(query_tokens)

    # Sort indices by descending score.
    ranked_indices = scores.argsort()[::-1]

    results = []

    for index in ranked_indices[:n]:

        results.append({
            "id": ids[index],
            "latin": sentences[index],
            "score": float(scores[index]),
        })

    return results


# ---------------------------------------------------------
# Interactive test
# ---------------------------------------------------------

if __name__ == "__main__":

    sentence = input("\nLatin sentence: ")

    results = find_similar_lexical(sentence, n=10)

    print("\nMost similar sentences:\n")

    for i, result in enumerate(results, start=1):

        print(f"{i}. Score: {result['score']:.4f}")
        print(f"   ID:    {result['id']}")
        print(f"   Latin: {result['latin']}")
        print()