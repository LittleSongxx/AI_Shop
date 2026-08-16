from __future__ import annotations

import math
import unicodedata

_FNV_OFFSET_BASIS = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3
_MASK_64 = (1 << 64) - 1
_SIGN_64 = 1 << 63


def local_hash_embedding(text: str, dimensions: int) -> list[float]:
    """Mirror Search's LocalHashEmbeddingModel for credential-free development."""

    if dimensions < 64:
        raise ValueError("local embedding dimensions must be at least 64")
    vector = [0.0] * dimensions
    normalized = _java_trim(unicodedata.normalize("NFKC", text or "").lower())
    if not normalized:
        vector[0] = 1.0
        return vector

    for term in _letter_number_terms(normalized):
        _add_feature(vector, f"word:{term}", 2.0)
        code_points = list(term)
        _add_ngrams(vector, code_points, 1, 0.35)
        _add_ngrams(vector, code_points, 2, 1.0)
        _add_ngrams(vector, code_points, 3, 0.7)
    return _normalize(vector)


def _java_trim(value: str) -> str:
    start = 0
    end = len(value)
    while start < end and ord(value[start]) <= 0x20:
        start += 1
    while end > start and ord(value[end - 1]) <= 0x20:
        end -= 1
    return value[start:end]


def _letter_number_terms(value: str) -> list[str]:
    terms: list[str] = []
    current: list[str] = []
    for character in value:
        if unicodedata.category(character)[:1] in {"L", "N"}:
            current.append(character)
            continue
        if current:
            terms.append("".join(current))
            current = []
    if current:
        terms.append("".join(current))
    return terms


def _add_ngrams(
    vector: list[float],
    code_points: list[str],
    size: int,
    weight: float,
) -> None:
    if len(code_points) < size:
        return
    for start in range(0, len(code_points) - size + 1):
        feature = f"char{size}:" + "".join(code_points[start : start + size])
        _add_feature(vector, feature, weight)


def _add_feature(vector: list[float], feature: str, weight: float) -> None:
    hash_value = _FNV_OFFSET_BASIS
    encoded = feature.encode("utf-16-be", errors="surrogatepass")
    for offset in range(0, len(encoded), 2):
        code_unit = (encoded[offset] << 8) | encoded[offset + 1]
        hash_value ^= code_unit
        hash_value = (hash_value * _FNV_PRIME) & _MASK_64
    mixed = (hash_value ^ (hash_value >> 32)) & 0xFFFFFFFF
    signed_mixed = mixed if mixed < (1 << 31) else mixed - (1 << 32)
    bucket = signed_mixed % len(vector)
    vector[bucket] += -weight if hash_value & _SIGN_64 else weight


def _normalize(vector: list[float]) -> list[float]:
    squared_norm = sum(value * value for value in vector)
    if squared_norm == 0.0:
        vector[0] = 1.0
        return vector
    norm = math.sqrt(squared_norm)
    return [value / norm for value in vector]
