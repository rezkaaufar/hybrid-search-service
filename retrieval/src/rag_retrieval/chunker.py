import re
from typing import Iterable, List, Tuple


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text


def split_paragraphs(text: str) -> List[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return paragraphs


def split_sentences(paragraph: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", paragraph)
    return [p.strip() for p in parts if p.strip()]


def chunk_text(text: str, chunk_size: int = 450, chunk_overlap: int = 80) -> List[Tuple[str, int]]:
    paragraphs = split_paragraphs(clean_text(text))
    chunks: List[Tuple[str, int]] = []
    current: List[str] = []
    current_tokens = 0

    def add_chunk():
        nonlocal current, current_tokens
        if not current:
            return
        chunk = " ".join(current).strip()
        chunks.append((chunk, current_tokens))
        if chunk_overlap > 0:
            overlap_tokens = 0
            overlap_parts: List[str] = []
            for sentence in reversed(split_sentences(chunk)):
                tokens = len(sentence.split())
                if overlap_tokens + tokens > chunk_overlap:
                    break
                overlap_parts.insert(0, sentence)
                overlap_tokens += tokens
            current = overlap_parts
            current_tokens = overlap_tokens
        else:
            current = []
            current_tokens = 0

    for para in paragraphs:
        sentences = split_sentences(para)
        for sentence in sentences:
            tokens = len(sentence.split())
            if current_tokens + tokens > chunk_size and current:
                add_chunk()
            current.append(sentence)
            current_tokens += tokens
        # paragraph boundary, commit if large block
        if current_tokens >= chunk_size:
            add_chunk()

    if current:
        add_chunk()

    return chunks
