from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from saltbox_ui.appsetup.http import Response, SetupError
from saltbox_ui.daemon import Services, create_app
from saltbox_ui.daemon import routes as daemon_routes
from saltbox_ui.daemon.updates import UpdateService
from saltbox_ui.layout import Layout
from saltbox_ui.releasenotes import (
    MAX_BODY,
    MAX_RELEASES,
    UPSTREAM,
    ReleaseNotes,
    between,
    clean_body,
    normalize,
    version_key,
)
from saltbox_ui.state.updates import ImageStatus


def release(tag: str, *, prerelease: bool = False, body: str = "", url: str = "") -> dict:
    return {
        "tag_name": tag,
        "name": tag,
        "prerelease": prerelease,
        "draft": False,
        "published_at": "2026-11-10T12:00:00Z",
        "html_url": url or f"https://github.com/Sonarr/Sonarr/releases/tag/{tag}",
        "body": body or f"Changes in {tag}",
    }


SONARR = [
    release("v4.0.13.2900", prerelease=True),
    release("v4.0.12.2800"),
    release("v4.0.11.2680"),
    release("v4.0.10.2544"),
    release("v4.0.9.2400"),
]


class Fake:
    def __init__(self, answers: list[Response]):
        self.answers = answers
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []

    def __call__(self, method, url, headers, body):
        self.urls.append(url)
        self.headers.append(headers)
        return self.answers.pop(0)


def ok(data) -> Response:
    return Response(200, json.dumps(data).encode())


@pytest.mark.parametrize(
    ("given", "plain"),
    [
        ("v4.0.11.2680", "4.0.11.2680"),
        ("release-4.0.11.2680", "4.0.11.2680"),
        ("3.0.2.5780-ls169", "3.0.2.5780"),
        ("4.5.1", "4.5.1"),
        ("version-1", "version-1"),
    ],
)
def test_versions_are_read_the_way_images_and_tags_write_them(given: str, plain: str) -> None:
    assert normalize(given) == plain


def test_version_keys_order_numerically() -> None:
    assert version_key("v4.0.10") > version_key("4.0.9")
    assert version_key("latest") is None


def test_an_update_brings_every_stable_release_after_the_current_one() -> None:
    chosen = between(SONARR, "4.0.9.2400", "4.0.12.2800")
    assert [r["tag_name"] for r in chosen] == ["v4.0.12.2800", "v4.0.11.2680", "v4.0.10.2544"]


def test_a_beta_counts_only_when_it_is_the_target() -> None:
    assert [r["tag_name"] for r in between(SONARR, "4.0.11.2680", "4.0.13.2900")] == [
        "v4.0.13.2900",
        "v4.0.12.2800",
    ]


def test_without_the_current_version_only_the_target_is_given() -> None:
    assert [r["tag_name"] for r in between(SONARR, "", "4.0.11.2680")] == ["v4.0.11.2680"]


def test_a_linuxserver_version_finds_the_upstream_release() -> None:
    prowlarr = [release("v3.0.2.5780"), release("v3.0.1.5752")]
    assert [r["tag_name"] for r in between(prowlarr, "3.0.1.5752-ls168", "3.0.2.5780-ls169")] == [
        "v3.0.2.5780"
    ]


def test_notes_are_plain_text_and_bounded() -> None:
    assert clean_body("Fixed\r\n<!-- hidden -->things\r") == "Fixed\nthings"
    long = clean_body("x" * (MAX_BODY + 50))
    assert len(long) <= MAX_BODY + 2 and long.endswith("…")


def test_notes_for_an_update_come_from_the_apps_github_project() -> None:
    fake = Fake([ok(SONARR)])
    notes = ReleaseNotes(transport=fake).for_update("sonarr", "4.0.10.2544", "4.0.12.2800")
    assert fake.urls == ["https://api.github.com/repos/Sonarr/Sonarr/releases?per_page=30"]
    assert fake.headers[0]["User-Agent"] == "saltbox-ui"
    assert "Authorization" not in fake.headers[0]
    assert notes["project"] == "Sonarr/Sonarr"
    assert notes["releases_url"] == "https://github.com/Sonarr/Sonarr/releases"
    assert [r["version"] for r in notes["releases"]] == ["4.0.12.2800", "4.0.11.2680"]
    assert notes["releases"][0]["body"] == "Changes in v4.0.12.2800"
    assert notes["error"] == "" and notes["more"] is False


def test_only_links_to_the_projects_own_release_pages_are_kept() -> None:
    bad = release("v4.0.11.2680", url="https://evil.example/releases/tag/v4.0.11.2680")
    other = release("v4.0.12.2800", url="https://github.com/Someone/Else/releases/tag/x")
    fake = Fake([ok([other, bad])])
    notes = ReleaseNotes(transport=fake).for_update("sonarr", "4.0.10", "4.0.12.2800")
    assert [r["url"] for r in notes["releases"]] == ["", ""]


def test_a_long_gap_shows_the_newest_and_says_there_is_more() -> None:
    many = [release(f"v1.{n}.0") for n in range(30, 0, -1)]
    notes = ReleaseNotes(transport=Fake([ok(many)])).for_update("sonarr", "1.0.0", "1.30.0")
    assert len(notes["releases"]) == MAX_RELEASES and notes["more"] is True
    assert notes["releases"][0]["version"] == "1.30.0"


def test_github_is_asked_once_per_project_and_failures_are_retried_sooner() -> None:
    now = [0.0]
    fake = Fake([Response(500), ok(SONARR), ok(SONARR)])
    notes = ReleaseNotes(transport=fake, clock=lambda: now[0])
    assert (
        notes.for_update("sonarr", "4.0.10", "4.0.11.2680")["error"] == "GitHub answered HTTP 500"
    )
    now[0] = 60
    assert notes.for_update("sonarr", "4.0.10", "4.0.11.2680")["error"]  # still the failure
    now[0] = 20 * 60
    assert notes.for_update("sonarr", "4.0.10", "4.0.11.2680")["releases"]
    now[0] = 3 * 3600
    notes.for_update("sonarr", "4.0.10", "4.0.11.2680")
    assert len(fake.urls) == 2  # the success is kept for hours
    now[0] = 7 * 3600
    notes.for_update("sonarr", "4.0.10", "4.0.11.2680")
    assert len(fake.urls) == 3


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (
            Response(403, b"{}", {"x-ratelimit-remaining": "0"}),
            "GitHub's limit on requests from this server is used up for now",
        ),
        (Response(404), "GitHub answered HTTP 404"),
        (Response(200, b"not json"), "GitHub's answer was not a list of releases"),
        (ok({"message": "Moved"}), "GitHub's answer was not a list of releases"),
    ],
)
def test_what_github_says_when_it_will_not_help(answer: Response, error: str) -> None:
    notes = ReleaseNotes(transport=Fake([answer])).for_update("sonarr", "1", "2")
    assert (notes["error"], notes["releases"]) == (error, [])


def test_an_unreachable_github_is_said_plainly() -> None:
    def down(*_):
        raise SetupError("cannot reach api.github.com: timed out")

    notes = ReleaseNotes(transport=down).for_update("radarr", "1", "2")
    assert notes["error"] == "cannot reach api.github.com: timed out"


def test_apps_without_a_known_project_ask_nothing() -> None:
    fake = Fake([])
    notes = ReleaseNotes(transport=fake).for_update("plex", "1", "2")
    assert notes == {
        "project": "",
        "releases_url": "",
        "releases": [],
        "more": False,
        "error": "",
    }
    assert fake.urls == []


def test_every_listed_project_is_a_plain_github_name() -> None:
    from saltbox_ui.releasenotes import PROJECT

    assert all(PROJECT.fullmatch(project) for project in UPSTREAM.values())


CONTAINERS = [
    {
        "name": "sonarr",
        "status": "running",
        "health": None,
        "managed": True,
        "image_ref": "ghcr.io/hotio/sonarr:release",
        "urls": [],
    },
    {
        "name": "radarr",
        "status": "running",
        "health": None,
        "managed": True,
        "image_ref": "ghcr.io/hotio/radarr:release",
        "urls": [],
    },
]


@pytest.fixture
def client(scratch_layout: Layout, fake_sb: Path, monkeypatch: pytest.MonkeyPatch):
    for name in ("sonarr", "radarr"):
        (scratch_layout.appdata / name).mkdir(parents=True)
    monkeypatch.setattr(daemon_routes, "_containers_or_none", lambda: [dict(c) for c in CONTAINERS])
    fake = Fake([ok(SONARR)])
    services = Services(scratch_layout, release_notes=ReleaseNotes(transport=fake))
    services.updates = UpdateService(
        scratch_layout,
        containers=lambda: CONTAINERS,
        checker=lambda containers: {
            "ghcr.io/hotio/sonarr:release": ImageStatus(
                "ghcr.io/hotio/sonarr:release",
                "update",
                containers=["sonarr"],
                local_version="4.0.10.2544",
                remote_version="4.0.11.2680",
            ),
            "ghcr.io/hotio/radarr:release": ImageStatus(
                "ghcr.io/hotio/radarr:release", "current", containers=["radarr"]
            ),
        },
        repos=lambda: [],
    )
    services.updates.refresh(wait=True)
    return TestClient(create_app(services=services)), fake


def test_the_app_page_gets_the_notes_for_its_waiting_update(client) -> None:
    http, _ = client
    notes = http.get("/api/apps/saltbox/sonarr/release-notes").json()
    assert [r["version"] for r in notes["releases"]] == ["4.0.11.2680"]
    assert notes["releases"][0]["url"] == (
        "https://github.com/Sonarr/Sonarr/releases/tag/v4.0.11.2680"
    )


def test_an_app_with_no_update_asks_github_nothing(client) -> None:
    http, fake = client
    notes = http.get("/api/apps/saltbox/radarr/release-notes").json()
    assert notes["releases"] == [] and notes["project"] == ""
    assert fake.urls == []
    assert http.get("/api/apps/saltbox/nope/release-notes").status_code == 404
