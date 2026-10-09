"""Recognising typed chat commands without hijacking interview answers.

Every command check runs before the interview sees the message, so a loose
match costs the user their answer: "we need search capabilities over our
contracts" used to get the help menu back because it contains "capabilities".
A command therefore has to *be* the message, not appear somewhere inside it:

- the message, after an optional polite lead-in ("please", "let's start",
  "can you show me the"), must **begin** with the trigger phrase, and
- whatever follows the trigger must be short (a record id, an initiative
  name, "please").

Pronoun-led sentences ("we rank use cases by cost today") are deliberately not
lead-ins: that is how people describe their process, not how they give orders.
"""

from __future__ import annotations

import re

#: Polite or framing openers stripped before looking for the trigger. Tried
#: longest first, repeatedly, so "ok so let's start the" peels away in steps.
_LEAD_INS = tuple(
    sorted(
        (
            "please",
            "ok",
            "okay",
            "now",
            "so",
            "hey",
            "can you",
            "could you",
            "can we",
            "could we",
            "i want to",
            "i'd like to",
            "id like to",
            "i would like to",
            "we want to",
            "we'd like to",
            "we would like to",
            "let's",
            "lets",
            "let us",
            "start",
            "begin",
            "run",
            "open",
            "show",
            "show me",
            "list",
            "give me",
            "kick off",
            "the",
            "a",
            "an",
        ),
        key=len,
        reverse=True,
    )
)

_PUNCT_RE = re.compile(r"[^\w\s'-]+")


def normalize_command(user_text: str | None) -> str:
    """Lower-cases, drops punctuation and collapses whitespace."""
    if not user_text:
        return ""
    text = user_text.lower().replace("\u2019", "'")
    return " ".join(_PUNCT_RE.sub(" ", text).split())


def match_command(
    user_text: str | None, triggers: tuple[str, ...], *, max_tail_words: int = 0
) -> str | None:
    """Returns the trigger the message opens with, or None.

    `max_tail_words` is how many words may follow the trigger: 0 for commands
    that take no argument, a few for "technical review <name or id>".
    """
    text = normalize_command(user_text)
    if not text:
        return None
    ordered = sorted(triggers, key=len, reverse=True)
    while True:
        for trigger in ordered:
            if text == trigger or text.startswith(trigger + " "):
                tail = text[len(trigger) :].split()
                if len(tail) <= max_tail_words:
                    return trigger
        for lead in _LEAD_INS:
            if text.startswith(lead + " "):
                text = text[len(lead) + 1 :]
                break
        else:
            return None
