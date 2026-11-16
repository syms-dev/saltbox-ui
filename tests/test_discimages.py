"""Films Radarr has as disc images, and having it find proper copies."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from saltbox_ui import discimages
from saltbox_ui.appsetup import AppSetup
from saltbox_ui.appsetup.discover import Service
from saltbox_ui.appsetup.http import Response
from saltbox_ui.daemon import Services, create_app
from saltbox_ui.layout import Layout


def quality(qid: int, name: str, allowed: bool) -> dict:
    return {"quality": {"id": qid, "name": name}, "items": [], "allowed": allowed}


TRASH = {  # a TRaSH profile: no DVD
    "id": 7,
    "name": "HD Bluray + WEB",
    "items": [
        quality(2, "DVD", False),
        {"name": "WEB 1080p", "items": [quality(3, "WEBDL-1080p", True)], "allowed": True},
        quality(7, "Bluray-1080p", True),
    ],
}
ANY = {  # Radarr's own "Any": DVD allowed
    "id": 1,
    "name": "Any",
    "items": [{"name": "DVD", "items": [quality(2, "DVD", True)], "allowed": True}],
}


def film(mid: int, title: str, file: str, profile: int, size: int = 8_000_000_000) -> dict:
    return {
        "id": mid,
        "title": title,
        "year": 2000 + mid,
        "qualityProfileId": profile,
        "hasFile": True,
        "movieFile": {"id": 100 + mid, "relativePath": file, "size": size},
    }


class FakeRadarr:
    def __init__(self) -> None:
        self.movies = [
            film(1, "Disc One", "Disc One (2001).iso", 7),
            film(2, "Fine Film", "Fine Film (2002) Bluray-1080p.mkv", 7),
            film(3, "Disc Two", "Disc Two (2003).IMG", 7),
            film(4, "On Any", "On Any (2004).iso", 1),
            {"id": 5, "title": "Missing", "qualityProfileId": 7, "hasFile": False},
        ]
        self.calls: list[tuple[str, str, object]] = []

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None) -> Response:
        assert headers["X-Api-Key"] == "radarrkey0123456789"
        path = url.split("/api/v3", 1)[1]
        data = json.loads(body) if body else None
        self.calls.append((method, path, data))
        if path == "/qualityprofile":
            return Response(200, json.dumps([TRASH, ANY]).encode())
        if path == "/movie":
            return Response(200, json.dumps(self.movies).encode())
        if (method, path) in (("POST", "/command"), ("DELETE", "/moviefile/bulk")):
            return Response(201 if method == "POST" else 200, b"{}")
        return Response(404, b"")


RADARR = Service("radarr", "radarr", 7878, True, api_key="radarrkey0123456789", host="radarr")


def test_disc_images_are_found_with_what_a_search_would_bring() -> None:
    assert discimages.allows_dvd(ANY) and not discimages.allows_dvd(TRASH)
    group_off = {"items": [{"name": "SD", "allowed": False, "items": [quality(2, "DVD", True)]}]}
    assert not discimages.allows_dvd(group_off)  # a quality is only as allowed as its group

    found = discimages.find(RADARR, FakeRadarr())
    assert [f["title"] for f in found["films"]] == ["Disc One", "Disc Two", "On Any"]
    assert [f["replaceable"] for f in found["films"]] == [True, True, False]
    assert found["blocked_by_profile"] == ["Any"] and found["size"] == 24_000_000_000
    assert found["films"][0] == {
        "movie_id": 1,
        "file_id": 101,
        "title": "Disc One",
        "year": 2001,
        "file": "Disc One (2001).iso",
        "size": 8_000_000_000,
        "profile": "HD Bluray + WEB",
        "replaceable": True,
    }


def test_a_search_keeps_the_images_and_a_delete_removes_only_the_replaceable_ones() -> None:
    radarr = FakeRadarr()
    assert discimages.search(RADARR, radarr) == {"searched": 2, "skipped": 1, "deleted": 0}
    assert ("POST", "/command", {"name": "MoviesSearch", "movieIds": [1, 3]}) in radarr.calls
    assert not any(method == "DELETE" for method, _, _ in radarr.calls)

    radarr = FakeRadarr()
    assert discimages.delete(RADARR, radarr) == {"searched": 2, "skipped": 1, "deleted": 2}
    changes = [(m, p, d) for m, p, d in radarr.calls if m != "GET"]
    assert changes == [
        ("DELETE", "/moviefile/bulk", {"movieFileIds": [101, 103]}),  # never "On Any"
        ("POST", "/command", {"name": "MoviesSearch", "movieIds": [1, 3]}),
    ]


@pytest.fixture
def client(scratch_layout: Layout, fake_sb: Path):
    folder = scratch_layout.appdata / "radarr"
    folder.mkdir(parents=True)
    (folder / "config.xml").write_text("<Config><ApiKey>radarrkey0123456789</ApiKey></Config>")
    radarr = FakeRadarr()
    services = Services(scratch_layout)
    services.app_setup_factory = lambda layout: AppSetup(
        layout, transport=radarr, resolver=lambda name: name
    )
    return TestClient(create_app(services=services)), radarr


def test_the_routes_work_out_the_films_themselves(client) -> None:
    http, radarr = client
    listed = http.get("/api/discimages").json()["instances"]
    assert listed[0]["name"] == "radarr" and len(listed[0]["films"]) == 3
    done = http.post("/api/discimages/replace", json={"instance": "radarr"}).json()
    assert done == {"searched": 2, "skipped": 1, "deleted": 0}
    deleted = http.post("/api/discimages/replace", json={"instance": "radarr", "delete": True})
    assert deleted.json()["deleted"] == 2
    assert http.post("/api/discimages/replace", json={"instance": "sonarr"}).status_code == 404
    # extra fields (a list of files to delete, say) are not taken from the request
    sneaky = {"instance": "radarr", "delete": True, "movieFileIds": [102]}
    http.post("/api/discimages/replace", json=sneaky)
    assert ("DELETE", "/moviefile/bulk", {"movieFileIds": [102]}) not in radarr.calls
