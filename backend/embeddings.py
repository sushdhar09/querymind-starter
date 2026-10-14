import hashlib
import math
import re

from chromadb import EmbeddingFunction


class HashEmbedding(EmbeddingFunction):
    DIM = 256

    def __init__(self):
        pass

    @staticmethod
    def name() -> str:
        return "querymind-hash"

    def get_config(self) -> dict:
        return {}

    @staticmethod
    def build_from_config(config: dict) -> "HashEmbedding":
        return HashEmbedding()

    def __call__(self, input):
        out = []
        for text in input:
            vec = [0.0] * self.DIM
            for tok in re.findall(r"[a-z0-9]+", text.lower()):
                vec[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.DIM] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


def get_embedding_function(mode: str):
    return HashEmbedding() if mode == "hash" else None
