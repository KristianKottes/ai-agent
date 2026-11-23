"""Align a saved English transcript to 16 kHz mono PCM with PocketSphinx."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

WORDS = re.compile(r"[a-z]+(?:'[a-z]+)?", re.IGNORECASE | re.ASCII)


@dataclass(frozen=True)
class AlignedWord:
    name: str
    start: int
    end: int
    phones: tuple[tuple[int, int, str], ...]


def align_english(pcm: bytes, transcript: str) -> Optional[list[AlignedWord]]:
    from pocketsphinx import Decoder

    words = WORDS.findall(transcript.lower())
    if not words or re.search(r"\d", transcript):
        return None
    decoder = Decoder(lm=None, loglevel="ERROR")
    if any(not decoder.lookup_word(word) for word in words):
        return None
    try:
        decoder.set_align_text(" ".join(words))
        decoder.start_utt()
        decoder.process_raw(pcm, full_utt=True)
        decoder.end_utt()
        decoder.set_alignment()
        decoder.start_utt()
        decoder.process_raw(pcm, full_utt=True)
        decoder.end_utt()
        alignment = decoder.get_alignment()
        if not alignment:
            return None
        result = [
            AlignedWord(word.name.split("(")[0], word.start, word.start + word.duration,
                        tuple((phone.start, phone.start + phone.duration, phone.name) for phone in word))
            for word in alignment
        ]
        if [word.name for word in result if not word.name.startswith("<")] == words:
            return result
    except RuntimeError:
        pass
    return None
