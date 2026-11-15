from cltk.data.fetch import FetchCorpus

corpus_downloader = FetchCorpus(language="lat")
corpus_downloader.import_corpus("lat_models_cltk")
