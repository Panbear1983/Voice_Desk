"""Spoken commands, one matcher for every door (Mac window and phone receiver).

The phrase lists live only in settings.py (GOODBYE_WORDS, SLEEP_WORDS, STOP_WORDS); this module
just normalises what was said and compares it. Peter 2026-11-02: "I want all the phrases to be
exactly the same as the Mac phrase to deactivate."
"""
from __future__ import annotations

from typing import Optional

import settings as S
import stt

GOODBYE_REPLY = "Goodbye."
GOODBYE_PROMPT = ("We are ending this voice session. File ONE short memory note of what we discussed and decided "
                  "(use mempalace_add_drawer under the right wing per ~/.mempalace/MEMORY_PROTOCOL.md; "
                  "wing 'peter' if it was general). Then reply with one short goodbye sentence.")


def _norm(text: str) -> str:
    # dictation on the phone may type a curly apostrophe ("that’s it"); fold it so it matches "that's it"
    return stt.normalize(text.replace("\u2019", "'").replace("\u2018", "'"))


def classify(text: str) -> Optional[str]:
    """"goodbye" | "sleep" | "stop" for a whole utterance that is one of the command phrases, else None."""
    norm = _norm(text)
    if not norm:
        return None
    for kind, words in (("goodbye", S.GOODBYE_WORDS), ("sleep", S.SLEEP_WORDS), ("stop", S.STOP_WORDS)):
        if norm in {_norm(w) for w in words}:
            return kind
    return None
