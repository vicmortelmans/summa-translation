from pathlib import Path
import sqlite3
import sys

corpus_dir = Path(sys.argv[1])
db = sqlite3.connect("latin_lemmas.sqlite")

db.executescript("""
CREATE TABLE IF NOT EXISTS FormLemma (
    form TEXT NOT NULL,
    lemma TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (form, lemma)
);
""")

counts = {}

for path in corpus_dir.rglob("*"):
    if not path.is_file():
        continue
    if path.suffix.lower() not in {".conll", ".conllu", ".txt"}:
        continue

    with path.open(encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()

            # Sentence boundaries and comments
            if not line or line.startswith("#"):
                continue

            fields = line.split("\t")
            if len(fields) < 3:
                continue

            token_id, form, lemma = fields[:3]

            # Skip CoNLL-U multiword-token and empty-node IDs
            if "-" in token_id or "." in token_id:
                continue

            if lemma in {"_", ""} or form in {"_", ""}:
                continue

            key = (form, lemma)
            counts[key] = counts.get(key, 0) + 1

db.executemany("""
INSERT INTO FormLemma (form, lemma, count)
VALUES (?, ?, ?)
ON CONFLICT(form, lemma)
DO UPDATE SET count = count + excluded.count
""", [
    (form, lemma, count)
    for (form, lemma), count in counts.items()
])

db.commit()

print("Distinct form-lemma pairs:", len(counts))
print("Database: latin_lemmas.sqlite")
db.close()

