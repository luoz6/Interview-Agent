class FakeEmbeddingProvider:
    provider_name = "fake"
    model_name = "fake-bge-m3"
    model_revision = "fake-v1"
    dimension = 3

    def embed_query(self, text):
        base = 0.1 if "redis" in text.lower() else 0.2
        return [base, base + 0.1, base + 0.2]

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]
