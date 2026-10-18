"""Shared embedding invariants for preparation and runtime retrieval."""
import math


def validate_vectors(vectors, count, dimension=None):
    if len(vectors) != count:
        raise ValueError("Embedding count mismatch")
    dimensions = {len(vector) for vector in vectors}
    if vectors and (len(dimensions) != 1 or 0 in dimensions):
        raise ValueError("Embedding dimensions mismatch")
    if vectors and dimension is not None and dimensions != {dimension}:
        raise ValueError("Embedding dimension differs from configured model")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
           for vector in vectors for value in vector):
        raise ValueError("Embedding contains a non-finite or non-numeric value")
    if any(not any(value != 0 for value in vector) for vector in vectors):
        raise ValueError("Embedding contains a zero vector")
