"""Films that Radarr has as disc images (``.iso``, ``.img``), and getting proper copies.

A disc image is a whole DVD or Blu-ray in one file. Plex, Bazarr and most players
cannot use it, and Radarr files it as DVD quality whatever is on the disc
(``MediaFileExtensions`` in Radarr's source). The TRaSH profiles Recyclarr sets
up do not allow DVD quality, so a film on one of them is due an upgrade, and a
search finds a proper release; Radarr replaces the image when it imports it.

``find`` lists the images. ``search`` asks Radarr to look for better copies and
keeps each image until one arrives. ``delete`` removes the images through
Radarr first (into its recycle bin, if one is set) for when the film should not
stay as an image while it waits. Films on a profile that still allows DVD are
left out of both: Radarr could fetch another disc image for them.
"""

from __future__ import annotations

from typing import Any

from .appsetup.discover import Service
from .appsetup.http import Client, SetupError, Transport

EXTENSIONS = (".iso", ".img")
DVD_QUALITIES = {2, 23}  # Radarr's "DVD" and "DVD-R": what a disc image counts as


def _client(service: Service, transport: Transport | None) -> Client:
    return Client(
        f"{service.host_url}/api/v3",
        headers={"X-Api-Key": service.api_key},
        transport=transport,
        name=service.name,
    )


def allows_dvd(profile: dict[str, Any]) -> bool:
    """Whether a Radarr quality profile accepts DVD quality, so a disc image, as a download."""

    def walk(items: Any, allowed_above: bool) -> bool:
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            allowed = bool(item.get("allowed")) and allowed_above
            quality = item.get("quality")
            if isinstance(quality, dict) and quality.get("id") in DVD_QUALITIES and allowed:
                return True
            if walk(item.get("items"), allowed):
                return True
        return False

    return walk(profile.get("items"), True)


def find(service: Service, transport: Transport | None = None) -> dict[str, Any]:
    """Every film one Radarr has as a disc image, with the profile it is on."""
    client = _client(service, transport)
    profiles = {
        p["id"]: p
        for p in client.get("/qualityprofile") or []
        if isinstance(p, dict) and isinstance(p.get("id"), int)
    }
    films = []
    for movie in client.get("/movie") or []:
        if not isinstance(movie, dict):
            continue
        file = movie.get("movieFile")
        if not isinstance(file, dict) or not isinstance(file.get("id"), int):
            continue
        path = str(file.get("relativePath") or file.get("path") or "")
        if not path.lower().endswith(EXTENSIONS):
            continue
        profile = profiles.get(movie.get("qualityProfileId")) or {}
        films.append(
            {
                "movie_id": movie.get("id"),
                "file_id": file["id"],
                "title": str(movie.get("title") or path),
                "year": movie.get("year"),
                "file": path,
                "size": int(file.get("size") or 0),
                "profile": str(profile.get("name") or "unknown"),
                # On a profile that still takes DVD quality, a search could bring another image.
                "replaceable": bool(profile) and not allows_dvd(profile),
            }
        )
    films.sort(key=lambda film: film["title"].lower())
    return {
        "name": service.name,
        "films": films,
        "size": sum(film["size"] for film in films),
        "blocked_by_profile": sorted(
            {film["profile"] for film in films if not film["replaceable"]}
        ),
    }


def search(service: Service, transport: Transport | None = None) -> dict[str, Any]:
    """Ask Radarr to find better copies of the replaceable images. Nothing is deleted."""
    found = find(service, transport)
    films = [film for film in found["films"] if film["replaceable"]]
    if films:
        _client(service, transport).post(
            "/command", {"name": "MoviesSearch", "movieIds": [f["movie_id"] for f in films]}
        )
    return {"searched": len(films), "skipped": len(found["films"]) - len(films), "deleted": 0}


def delete(service: Service, transport: Transport | None = None) -> dict[str, Any]:
    """Delete the replaceable images through Radarr, then search for proper copies."""
    found = find(service, transport)
    films = [film for film in found["films"] if film["replaceable"]]
    if not films:
        return {"searched": 0, "skipped": len(found["films"]), "deleted": 0}
    client = _client(service, transport)
    client.request(
        "DELETE", "/moviefile/bulk", json_body={"movieFileIds": [f["file_id"] for f in films]}
    )
    try:
        client.post(
            "/command", {"name": "MoviesSearch", "movieIds": [f["movie_id"] for f in films]}
        )
    except SetupError as error:
        raise SetupError(
            f"the images were deleted, but the search did not start: {error}"
        ) from error
    return {
        "searched": len(films),
        "skipped": len(found["films"]) - len(films),
        "deleted": len(films),
    }
