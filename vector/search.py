import os
from pathlib import Path

from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

DB_DIR = Path(__file__).resolve().parent / "vector_db"
COLLECTION_NAME = "latin_sentences"
MODEL_NAME = "BAAI/bge-m3"


# ---------------------------------------------------------
# Load database and model
# ---------------------------------------------------------

client = QdrantClient(path=str(DB_DIR))

TOKEN_FILE = Path(__file__).resolve().parent.parent / ".hf_token"   # repo root

def _load_hf_token() -> str | None:
    token = os.environ.get("HF_TOKEN")
    if token:
        return token.strip()

    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token

    return None


hf_token = _load_hf_token()
if hf_token:
    os.environ["HF_TOKEN"] = hf_token          # harmless; other HF libs pick it up too

model = SentenceTransformer(MODEL_NAME, token=hf_token)   # explicit, no ordering surprise


# ---------------------------------------------------------
# Search function
# ---------------------------------------------------------

def find_similar(
    latin_sentence: str,
    n: int = 5,
    score_threshold: float | None = None,
):
    """
    Find Latin sentences semantically similar to latin_sentence.

    Returns a list of dictionaries containing:
        latin
        dutch
        source
        score
    """

    query_vector = model.encode(
        latin_sentence,
        normalize_embeddings=True,
    ).tolist()

    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=n,
        score_threshold=score_threshold,
    ).points

    output = []

    for result in results:

        item = {
            "score": result.score,
            "latin": result.payload["latin"],
            "dutch": result.payload["dutch"],
        }

        if "source" in result.payload:
            item["source"] = result.payload["source"]

        output.append(item)

    return output


# ---------------------------------------------------------
# Interactive test
# ---------------------------------------------------------

if __name__ == "__main__":
    try:
        sentence = input("\nLatin sentence: ")

        results = find_similar(sentence, n=5)

        print("\nMost similar sentences:\n")

        for i, result in enumerate(results, start=1):

            print(f"{i}. Score: {result['score']:.4f}")
            print(f"   Latin: {result['latin']}")
            print(f"   Dutch: {result['dutch']}")

            if "source" in result:
                print(f"   Source: {result['source']}")

            print()
    finally:
        client.close()