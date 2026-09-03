"""Разбиение документа на смысловые куски.

Режем по границам абзацев, а не по фиксированному числу символов: правило,
разорванное посередине предложения, даёт бесполезный фрагмент, на котором
модель не может построить ответ.

Абзац, который сам по себе длиннее лимита, режется по предложениям.
Между соседними кусками делается небольшое перекрытие, чтобы правило,
попавшее на стык, осталось целым хотя бы в одном фрагменте.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

DEFAULT_MAX_TOKENS = 400
DEFAULT_OVERLAP_TOKENS = 40

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    token_count: int
    content_hash: str


_encoder = None
_encoder_ready = False


def _get_encoder():
    """tiktoken по возможности; без него — приблизительный счёт по словам."""
    global _encoder, _encoder_ready
    if _encoder_ready:
        return _encoder
    _encoder_ready = True
    try:
        import tiktoken

        _encoder = tiktoken.get_encoding("cl100k_base")
    except Exception:  # pragma: no cover - зависит от окружения
        _encoder = None
    return _encoder


def count_tokens(text: str) -> int:
    encoder = _get_encoder()
    if encoder is not None:
        return len(encoder.encode(text))
    # запасной вариант: для кириллицы примерно 1 токен на 3 символа
    return max(1, len(text) // 3)


def hash_text(text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _split_long_paragraph(paragraph: str, max_tokens: int) -> list[str]:
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(paragraph) if s.strip()]
    parts: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        tokens = count_tokens(sentence)
        if current and current_tokens + tokens > max_tokens:
            parts.append(" ".join(current))
            current, current_tokens = [], 0
        current.append(sentence)
        current_tokens += tokens

    if current:
        parts.append(" ".join(current))
    return parts or [paragraph]


def split_text(
    text: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Документ -> список кусков. Пустой текст даёт пустой список."""
    if not text or not text.strip():
        return []

    blocks: list[str] = []
    for paragraph in _PARAGRAPH_SPLIT.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if count_tokens(paragraph) > max_tokens:
            blocks.extend(_split_long_paragraph(paragraph, max_tokens))
        else:
            blocks.append(paragraph)

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for block in blocks:
        tokens = count_tokens(block)
        if current and current_tokens + tokens > max_tokens:
            chunks.append("\n\n".join(current))
            # перекрытие: последний блок уходит и в следующий кусок,
            # чтобы правило на стыке не потерялось
            if overlap_tokens > 0 and count_tokens(current[-1]) <= overlap_tokens:
                current = [current[-1]]
                current_tokens = count_tokens(current[0])
            else:
                current, current_tokens = [], 0
        current.append(block)
        current_tokens += tokens

    if current:
        chunks.append("\n\n".join(current))

    return [
        Chunk(
            index=index,
            text=chunk_text,
            token_count=count_tokens(chunk_text),
            content_hash=hash_text(chunk_text),
        )
        for index, chunk_text in enumerate(chunks)
    ]
