from cltk.lemmatize.lat import LatinBackoffLemmatizer

lemmatizer = LatinBackoffLemmatizer()

tokens = ["domini", "fuerunt", "dixerat", "iustitiae"]

print(lemmatizer.lemmatize(tokens))
