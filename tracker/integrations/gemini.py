"""Gemini text generation - powers Dashboard's "What should I watch?" box.
Bring-your-own-key and per-profile (Settings), not instance-wide like
Trakt/Simkl/TMDB in InstanceConfig - this is a personal ask, not a shared
sync. A plain REST call via requests, same convention as every other
integration module here - no Google client library dependency.

Verified against Gemini's publicly documented REST API shape, not against
a live account from this environment. Every function here silently
returns None on any failure (no key, bad key, quota exceeded, network
error, an unexpected response shape) - a recommendation failing never
blocks anything else on the page it's attached to."""

import logging

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
MODEL = "gemini-2.0-flash"


def generate(api_key, prompt):
    """Returns the model's plain-text reply, or None on failure."""
    if not api_key:
        return None
    try:
        resp = requests.post(
            f"{API_BASE}/models/{MODEL}:generateContent",
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=20,
        )
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("Gemini request failed", exc_info=True)
        return None
    try:
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        logger.warning("Gemini response had an unexpected shape", exc_info=True)
        return None


def build_recommendation_prompt(profile, mood):
    """Grounds the ask in this profile's own watch history/genre taste
    (best-effort - an empty library just means a shorter, taste-blind
    prompt, not an error) plus their free-text mood, e.g. "something
    light after a long day"."""
    from tracker import selectors
    from tracker.models import MediaType

    recent_events = selectors.library_history(
        profile, [MediaType.MOVIE, MediaType.TV, MediaType.ANIME], limit=15
    )
    recent_titles = []
    for event in recent_events:
        if event.title.name not in recent_titles:
            recent_titles.append(event.title.name)
    genres = [g["name"] for g in selectors.top_genres(profile, limit=5)]

    lines = [
        "You are a movie/TV recommendation assistant inside a self-hosted "
        "watch tracker called Spool.",
        "Give 3-5 specific movie or TV recommendations based on what this "
        "person has watched before and what they're in the mood for right "
        "now. Keep it conversational and brief - for each pick, give the "
        "title, the year if you know it, and a one-sentence reason it fits.",
    ]
    if recent_titles:
        lines.append(f"They've recently watched: {', '.join(recent_titles)}.")
    if genres:
        lines.append(f"Their favorite genres: {', '.join(genres)}.")
    lines.append(f"What they're in the mood for right now: {mood}")
    return "\n".join(lines)
