from cltk.lemmatize.lat import LatinBackoffLemmatizer

lemmatizer = LatinBackoffLemmatizer()

tokens = ["appetitus"]

print(lemmatizer.lemmatize(tokens))
