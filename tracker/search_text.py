"""Text normalisation and ranking shared by every search in the app (the
TMDB half in integrations/tmdb.py and the library half in selectors.py).

Pure on purpose - no ORM, no network - so what counts as "the same title"
is one small, unit-tested definition: lowercase, accents and punctuation
gone, "&" read as "and", whitespace collapsed. "Spider-Man", "spider man"
and "Spider‑Man!" all normalise to the same words; "spiderman" matches them
through compact() (the same text with the spaces removed)."""

import re
import unicodedata

_APOSTROPHES = re.compile(r"['’‘`]")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize(text):
    """"Pokémon: The Movie!" -> "pokemon the movie". Accents stripped,
    apostrophes dropped (so "don't" is "dont", not "don t"), "&" read as
    "and", every other run of punctuation/whitespace one space."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _APOSTROPHES.sub("", text.lower()).replace("&", " and ")
    return _NON_ALNUM.sub(" ", text).strip()


def tokens(text):
    return normalize(text).split()


def compact(text):
    """normalize() with the spaces removed - "Spider-Man" and "spiderman"
    both become "spiderman"."""
    return normalize(text).replace(" ", "")


# match tiers, best first
EXACT, PREFIX, ALL_TOKENS, OTHER = 0, 1, 2, 3


_LEADING_ARTICLE = re.compile(r"^(?:the|a|an) (?=.)")


def _without_article(normalized):
    return _LEADING_ARTICLE.sub("", normalized)


def match_tier(queries, name):
    """How well `name` answers any of `queries` (the typed text plus its
    variants): exact normalised match, then name starts with the query,
    then every query word appears in the name, then anything else. Compared
    both word-wise and with spaces removed, so "spiderman" is an exact
    match for "Spider-Man", and ignoring a leading "the"/"a"/"an", so "The
    Avengers" is an exact match for "avengers"."""
    name_norm = _without_article(normalize(name))
    name_compact, name_tokens = name_norm.replace(" ", ""), set(name_norm.split())
    best = OTHER
    for query in queries:
        q_norm = _without_article(normalize(query))
        q_compact = q_norm.replace(" ", "")
        if not q_compact:
            continue
        if q_norm == name_norm or q_compact == name_compact:
            return EXACT
        if name_norm.startswith(q_norm) or name_compact.startswith(q_compact):
            best = min(best, PREFIX)
        elif set(q_norm.split()) <= name_tokens or q_compact in name_compact:
            best = min(best, ALL_TOKENS)
    return best
