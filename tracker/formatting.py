"""Text formats that must read the same on every page."""


def episode_code(season, episode, episode_to=None, season_to=None):
    """The one episode format: "S1E9", and "S1E1–S1E5" for a range (an en dash,
    the second half always spelled out in full). No zero padding, no space, no
    colon. Returns "" when the season or episode is missing."""
    if season in (None, "") or episode in (None, ""):
        return ""
    code = f"S{season}E{episode}"
    if episode_to in (None, ""):
        return code
    end_season = season if season_to in (None, "") else season_to
    if (end_season, episode_to) == (season, episode):
        return code
    return f"{code}–S{end_season}E{episode_to}"
