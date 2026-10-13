"""Emotion and action tags.

The model is asked to start its reply with a tag such as ``[emotion:happy]`` and may
switch mood before a later sentence. It can also ask Pom to move with an action tag
such as ``[action:dance]``. ``EmotionStream`` pulls those tags out of the token stream
as they arrive, so the desktop app can change Pom's face and body immediately while
the user only sees clean text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

EMOTIONS = (
    "neutral", "happy", "joyful", "laughing", "love", "excited", "surprised", "curious",
    "thinking", "sad", "crying", "angry", "scared", "shy", "confused", "smug", "proud",
    "bored", "sleepy", "dizzy", "wink", "hungry",
)
_ALIASES = {"joy": "joyful", "laugh": "laughing", "calm": "neutral", "sorry": "sad", "worried": "scared",
            "embarrassed": "shy", "playful": "wink", "amazed": "surprised", "think": "thinking"}
ACTIONS = ("dance", "jump", "spin", "wave", "nod", "shake_head", "celebrate", "walk", "run", "hide", "sit", "stretch", "bounce")
_ACTION_ALIASES = {"party": "celebrate", "hop": "jump", "twirl": "spin", "hello": "wave", "yes": "nod", "no": "shake_head",
                   "zoom": "run", "zoomies": "run", "peek": "hide", "rest": "sit"}
TAG_RE = re.compile(r"\[\s*(emotion|action)\s*:\s*([a-zA-Z_-]{2,20})\s*\]\s*", re.I)
_MAX_TAG = 28


def normalize(name: str) -> str:
    n = name.strip().lower()
    n = _ALIASES.get(n, n)
    return n if n in EMOTIONS else "neutral"


def normalize_action(name: str) -> str | None:
    n = name.strip().lower().replace("-", "_")
    n = _ACTION_ALIASES.get(n, n)
    return n if n in ACTIONS else None


@dataclass
class Piece:
    kind: str  # "text", "emotion" or "action"
    value: str


class EmotionStream:
    def __init__(self) -> None:
        self._buf = ""
        self.first: str | None = None
        self.last: str | None = None
        self.actions: list[str] = []

    def feed(self, chunk: str) -> list[Piece]:
        self._buf += chunk
        out: list[Piece] = []
        while True:
            m = TAG_RE.search(self._buf)
            if m:
                if m.start() > 0:
                    out.append(Piece("text", self._buf[: m.start()]))
                if m.group(1).lower() == "action":
                    act = normalize_action(m.group(2))
                    if act:
                        self.actions.append(act)
                        out.append(Piece("action", act))
                else:
                    emo = normalize(m.group(2))
                    self.first = self.first or emo
                    self.last = emo
                    out.append(Piece("emotion", emo))
                self._buf = self._buf[m.end():]
                continue
            # hold back a possible partial tag at the end of the buffer
            cut = self._buf.rfind("[")
            if cut != -1 and len(self._buf) - cut < _MAX_TAG and "]" not in self._buf[cut:]:
                if cut > 0:
                    out.append(Piece("text", self._buf[:cut]))
                self._buf = self._buf[cut:]
            elif self._buf:
                out.append(Piece("text", self._buf))
                self._buf = ""
            break
        return [p for p in out if p.value]

    def flush(self) -> list[Piece]:
        rest, self._buf = self._buf, ""
        return [Piece("text", rest)] if rest else []


def strip_tags(text: str) -> str:
    return TAG_RE.sub("", text).strip()


_HINTS = [
    ("crying", r"\b(so sorry for your loss|heartbroken)\b"),
    ("sad", r"\b(sorry|unfortunately|sad)\b"),
    ("excited", r"\b(awesome|amazing|congrat\w*|woo+)\b|!{2,}"),
    ("happy", r"\b(glad|great|sure|happy)\b"),
    ("thinking", r"\b(let me think|hmm+)\b"),
]


def guess(text: str) -> str:
    """Fallback when the model forgets the tag."""
    low = text.lower()
    for emo, pat in _HINTS:
        if re.search(pat, low):
            return emo
    return "neutral"
