from pathlib import Path

import pandas as pd
from tqdm import tqdm
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

CSV_FILE = Path("latin_nl_align.txt")
DB_DIR = Path("vector_db")

COLLECTION_NAME = "latin_sentences"

# Good starting point for multilingual semantic search.
# Keep this configurable so you can experiment later.
MODEL_NAME = "BAAI/bge-m3"

BATCH_SIZE = 64


# ---------------------------------------------------------
# Load data
# ---------------------------------------------------------

print(f"Loading {CSV_FILE}...")

df = pd.read_csv(CSV_FILE, sep="\t", header=None, names=["latin", "dutch", "score"])

df = df.dropna(subset=["latin", "dutch", "score"])

# Generate IDs for each row
df.insert(0, "id", range(1, len(df) + 1))

print(f"Loaded {len(df):,} sentence pairs.")


# ---------------------------------------------------------
# Load embedding model
# ---------------------------------------------------------

print(f"Loading embedding model: {MODEL_NAME}")

model = SentenceTransformer(MODEL_NAME)

vector_size = model.get_sentence_embedding_dimension()

print(f"Embedding dimension: {vector_size}")


# ---------------------------------------------------------
# Create local Qdrant database
# ---------------------------------------------------------

DB_DIR.mkdir(parents=True, exist_ok=True)

client = QdrantClient(path=str(DB_DIR))

# Recreate the collection every time this script is run.
# This is convenient while developing.
if client.collection_exists(COLLECTION_NAME):
    print("Deleting existing collection...")
    client.delete_collection(COLLECTION_NAME)

client.create_collection(
    collection_name=COLLECTION_NAME,
    vectors_config=VectorParams(
        size=vector_size,
        distance=Distance.COSINE,
    ),
)


# ---------------------------------------------------------
# Generate embeddings and insert into Qdrant
# ---------------------------------------------------------

print("Generating embeddings...")

for start in tqdm(
    range(0, len(df), BATCH_SIZE),
    desc="Embedding",
):
    batch = df.iloc[start:start + BATCH_SIZE]

    latin_sentences = batch["latin"].tolist()

    embeddings = model.encode(
        latin_sentences,
        batch_size=BATCH_SIZE,
        normalize_embeddings=True,
        show_progress_bar=False,
    )

    points = []

    for row, embedding in zip(batch.itertuples(index=False), embeddings):

        payload = {
            "latin": row.latin,
            "dutch": row.dutch,
            "score": row.score,
        }

        # Add source if it exists in the TSV.
        if hasattr(row, "source"):
            payload["source"] = row.source

        points.append(
            PointStruct(
                id=int(row.id),
                vector=embedding.tolist(),
                payload=payload,
            )
        )

    client.upsert(
        collection_name=COLLECTION_NAME,
        points=points,
    )


# ---------------------------------------------------------
# Done
# ---------------------------------------------------------

print()
print("Database created successfully.")
print(f"Location:   {DB_DIR}")
print(f"Collection: {COLLECTION_NAME}")
print(f"Sentences:  {len(df):,}")