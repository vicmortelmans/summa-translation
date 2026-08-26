from pathlib import Path

from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

DB_DIR = Path("vector_db")
COLLECTION_NAME = "latin_sentences"
MODEL_NAME = "BAAI/bge-m3"


# ---------------------------------------------------------
# Load database and model
# ---------------------------------------------------------

client = QdrantClient(path=str(DB_DIR))

model = SentenceTransformer(MODEL_NAME)


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