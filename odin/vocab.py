"""Vocabulary validation against the Vosk lexicon.

Vosk grammars can only contain words present in the acoustic model's lexicon.
A word that is missing is silently dropped from the grammar, which would make
the phrase containing it permanently unrecognisable. Every phrase ODIN knows is
checked through here, so a typo or an out-of-vocabulary app alias is caught at
startup instead of failing mysteriously when spoken.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from typing import Iterable

_WORD_RE = re.compile(r"[a-z']+")


def words_in(phrases: Iterable[str]) -> set[str]:
    """Every distinct word used across the given phrases."""
    found: set[str] = set()
    for phrase in phrases:
        if phrase.startswith("["):  # vosk meta token such as [unk]
            continue
        found.update(_WORD_RE.findall(phrase.lower()))
    return found


def missing_words(model, words: Iterable[str], sample_rate: int = 16000) -> list[str]:
    """Return the subset of `words` that the model's lexicon does not contain.

    Vosk reports out-of-vocabulary words only as a C-level log line on stderr,
    so stderr is redirected at the file-descriptor level to capture it.
    """
    from vosk import KaldiRecognizer

    missing: list[str] = []
    err_fd = sys.stderr.fileno()
    sys.stderr.flush()
    saved = os.dup(err_fd)
    try:
        for word in sorted(set(words)):
            with tempfile.TemporaryFile() as sink:
                os.dup2(sink.fileno(), err_fd)
                try:
                    KaldiRecognizer(model, sample_rate, json.dumps([word, "[unk]"]))
                finally:
                    sys.stderr.flush()
                    os.dup2(saved, err_fd)
                sink.seek(0)
                log = sink.read().decode("utf-8", "replace")
            if "missing in vocabulary" in log:
                missing.append(word)
    finally:
        os.close(saved)
    return missing


def check_phrases(model, phrases: Iterable[str]) -> list[str]:
    """Out-of-vocabulary words used by the given phrases."""
    return missing_words(model, words_in(phrases))
