"""ESPN provider: fetch a team's schedule and turn events into our Match fields.

ESPN's API is unofficial and can change without notice, so everything that
touches it lives in this one file.
"""
from datetime import datetime, timezone
from typing import Optional
from functools import lru_cache


import httpx

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports"


def fetch_team_schedule(
    league_path: str, espn_team_id: str, season: int, seasontype: int = 2
) -> list[dict]:
    """Return the raw list of events for one team.

    league_path looks like "football/nfl". seasontype: 1 preseason,
    2 regular season, 3 postseason.
    """
    url = f"{BASE_URL}/{league_path}/teams/{espn_team_id}/schedule"
    response = httpx.get(
        url, params={"season": season, "seasontype": seasontype}, timeout=10
    )
    response.raise_for_status()
    return response.json().get("events", [])


def parse_espn_date(value: str) -> datetime:
    """Parse ESPN timestamps like "2026-11-08T21:25Z" into aware UTC datetimes."""
    for fmt in ("%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized ESPN date format: {value!r}")


def parse_event(
    event: dict, espn_team_id: str, competition: str = "NFL"
) -> Optional[dict]:
    """Convert one ESPN event into the fields our Match needs, from the
    point of view of the tracked team. Returns None if the event can't be used.
    """
    competitions = event.get("competitions") or []
    if not competitions:
        return None

    competitors = competitions[0].get("competitors", [])
    ours = next((c for c in competitors if c["team"]["id"] == espn_team_id), None)
    theirs = next((c for c in competitors if c["team"]["id"] != espn_team_id), None)
    if ours is None or theirs is None:
        return None

    return {
        "external_id": event["id"],
        "opponent": theirs["team"]["displayName"],
        "is_home": ours["homeAway"] == "home",
        "competition": competition,
        "kickoff_utc": parse_espn_date(event["date"]),
    }


@lru_cache(maxsize=None)
def _fetch_teams(league_path: str) -> tuple[dict, ...]:
    response = httpx.get(f"{BASE_URL}/{league_path}/teams", timeout=10)
    response.raise_for_status()
    leagues = response.json()["sports"][0]["leagues"]
    return tuple(item["team"] for item in leagues[0]["teams"])


def find_team(league_path: str, name: str) -> list[dict]:
    wanted = name.strip().casefold()
    keys = ("displayName", "shortDisplayName", "nickname", "abbreviation")
    return [
        t for t in _fetch_teams(league_path)
        if any(str(t.get(k, "")).casefold() == wanted for k in keys)
    ]
