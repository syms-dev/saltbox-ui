"""Uninstalling an app: containers go, settings only on request, nothing else breaks."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from saltbox_ui import uninstall
from saltbox_ui.daemon import Services, create_app
from saltbox_ui.daemon import routes as daemon_routes
from saltbox_ui.layout import Layout


def container(name: str, network: str = "saltbox", depends_on: str = "") -> dict:
    return {
        "name": name,
        "status": "running",
        "health": None,
        "managed": True,
        "image_ref": f"example/{name}:latest",
        "network_mode": network,
        "urls": [],
        "labels": {"com.github.saltbox.depends_on": depends_on} if depends_on else {},
    }


CONTAINERS = [
    container("lidarr"),
    container("gluetun"),
    container("qbittorrent", network="container:gluetun"),
    container("traefik"),
    container("authelia", depends_on="authelia-redis"),
    container("authelia-redis"),
    container("petio", depends_on="petio-mongo"),
    container("petio-mongo"),
]


def test_dependents_names_what_would_break() -> None:
    assert uninstall.dependents({"lidarr"}, CONTAINERS) == []
    assert uninstall.dependents({"gluetun"}, CONTAINERS) == [
        "qbittorrent runs inside gluetun's network"
    ]
    assert uninstall.dependents({"authelia-redis"}, CONTAINERS) == [
        "authelia needs authelia-redis to start"
    ]
    # Removing an app together with what it brought along breaks nothing.
    assert uninstall.dependents({"petio", "petio-mongo"}, CONTAINERS) == []


def test_only_a_plain_folder_directly_under_appdata_can_be_deleted(tmp_path: Path) -> None:
    appdata = tmp_path / "opt"
    (appdata / "lidarr").mkdir(parents=True)
    (appdata / "lidarr" / "config.xml").write_text("<Config/>")
    outside = tmp_path / "precious"
    outside.mkdir()
    (appdata / "linked").symlink_to(outside)
    (appdata / "file").write_text("not a folder")
    assert uninstall.data_folder(appdata, "lidarr") == appdata / "lidarr"
    for name in ("linked", "file", "missing", "..", ".", "", "../precious", "lidarr/..", "a b"):
        assert uninstall.data_folder(appdata, name) is None, name
        with pytest.raises(uninstall.UninstallError):
            uninstall.delete_folder(appdata, name)
    assert outside.is_dir() and (appdata / "lidarr" / "config.xml").exists()
    assert uninstall.delete_folder(appdata, "lidarr") == appdata / "lidarr"
    assert not (appdata / "lidarr").exists() and outside.is_dir()


@pytest.fixture
def box(scratch_layout: Layout, fake_sb: Path, monkeypatch: pytest.MonkeyPatch):
    running = [dict(c) for c in CONTAINERS]
    removed: list[str] = []

    def remove(name: str, runner=None) -> None:
        removed.append(name)
        running[:] = [c for c in running if c["name"] != name]

    monkeypatch.setattr(daemon_routes, "_containers_or_none", lambda: [dict(c) for c in running])
    monkeypatch.setattr(uninstall, "remove_container", remove)
    for name in ("lidarr", "gluetun", "traefik", "authelia", "petio"):
        (scratch_layout.appdata / name).mkdir(parents=True)
    (scratch_layout.appdata / "lidarr" / "lidarr.db").write_text("data")
    services = Services(scratch_layout)
    monkeypatch.setattr(services.updates, "refresh", lambda **_: False)
    return TestClient(create_app(services=services)), scratch_layout, removed


def test_uninstall_keeps_settings_unless_told_otherwise(box) -> None:
    client, layout, removed = box
    plan = client.get("/api/apps/saltbox/lidarr/uninstall").json()
    assert plan == {
        "containers": ["lidarr"],
        "folders": [str(layout.appdata / "lidarr")],
        "instances": ["lidarr"],
        "blockers": [],
        "references": [],
    }
    done = client.post("/api/apps/saltbox/lidarr/uninstall", json={}).json()
    assert done == {
        "removed": ["lidarr"],
        "deleted": [],
        "kept": [str(layout.appdata / "lidarr")],
        "cleaned": [],
        "not_cleaned": [],
    }
    assert removed == ["lidarr"] and (layout.appdata / "lidarr" / "lidarr.db").exists()
    apps = {a["role"]: a for a in client.get("/api/apps").json()}
    assert apps["lidarr"]["containers"] == [] and apps["lidarr"]["installed"] is True

    # A second pass, with the settings: the app is then gone from the installed list.
    again = client.post("/api/apps/saltbox/lidarr/uninstall", json={"delete_data": True}).json()
    assert again["removed"] == [] and again["kept"] == []
    assert again["deleted"] == [str(layout.appdata / "lidarr")]
    assert not (layout.appdata / "lidarr").exists()
    apps = {a["role"]: a for a in client.get("/api/apps").json()}
    assert apps["lidarr"]["installed"] is False
    assert client.post("/api/apps/saltbox/lidarr/uninstall", json={}).status_code == 409


def test_uninstall_refuses_what_other_things_need(box) -> None:
    client, layout, removed = box
    gluetun = client.post("/api/apps/saltbox/gluetun/uninstall", json={"delete_data": True})
    assert gluetun.status_code == 409
    assert "qbittorrent runs inside gluetun's network" in gluetun.json()["detail"]
    for role in ("traefik", "authelia"):
        refused = client.post(f"/api/apps/saltbox/{role}/uninstall", json={})
        assert refused.status_code == 409 and "part of Saltbox itself" in refused.json()["detail"]
    assert client.post("/api/apps/saltbox/nope/uninstall", json={}).status_code == 404
    assert removed == [] and (layout.appdata / "gluetun").is_dir()


UNPACKERR_CONF = """\
## Unpackerr settings, as Saltbox's template writes them
debug = false

[[sonarr]]
  url = "https://sonarr.example.com"
  api_key = "sonarrkey0123456789"
  paths = ["/mnt/unionfs/downloads/torrents/qbittorrent/completed"]
  protocols = "torrent"

[[lidarr]]
  url = "https://lidarr.example.com"
  api_key = "lidarrkey0123456789"
  paths = ["/mnt/unionfs/downloads/torrents/qbittorrent/completed"]
  protocols = "torrent"

################
# Publications #
################
#[[readarr]]
#  url = "http://127.0.0.1:8787"

[webserver]
  metrics = false
"""


def test_unpackerr_sections_are_found_and_removed_whole() -> None:
    from saltbox_ui.references import remove_unpackerr_sections, unpackerr_sections

    sections = unpackerr_sections(UNPACKERR_CONF)
    assert [(s["role"], s["api_key"]) for s in sections] == [
        ("sonarr", "sonarrkey0123456789"),
        ("lidarr", "lidarrkey0123456789"),
    ]
    left = remove_unpackerr_sections(UNPACKERR_CONF, lambda s: s["role"] == "lidarr")
    assert "[[lidarr]]" not in left and "lidarrkey" not in left
    assert "[[sonarr]]" in left and "sonarrkey0123456789" in left
    assert "#[[readarr]]" in left and "[webserver]" in left and "metrics = false" in left
    assert 'protocols = "torrent"\n\n################' in left  # spacing kept


def test_uninstalling_an_app_takes_it_out_of_unpackerr_and_prowlarr(
    scratch_layout: Layout, fake_sb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saltbox writes Unpackerr's settings once, from what was installed then, and never
    again; Connect apps adds each *arr to Prowlarr. Removing Lidarr left both pointing at
    an app that was gone."""
    import json

    from saltbox_ui.appsetup import AppSetup
    from saltbox_ui.appsetup.http import Response

    running = [dict(c) for c in CONTAINERS] + [container("unpackerr"), container("prowlarr")]
    monkeypatch.setattr(daemon_routes, "_containers_or_none", lambda: [dict(c) for c in running])
    monkeypatch.setattr(uninstall, "remove_container", lambda name, runner=None: None)
    restarted: list[str] = []
    monkeypatch.setattr(
        daemon_routes, "container_action", lambda name, action: restarted.append(f"{action} {name}")
    )
    appdata = scratch_layout.appdata
    for name in ("lidarr", "sonarr", "unpackerr", "prowlarr"):
        (appdata / name).mkdir(parents=True)
    (appdata / "lidarr" / "config.xml").write_text(
        "<Config><ApiKey>lidarrkey0123456789</ApiKey></Config>"
    )
    (appdata / "sonarr" / "config.xml").write_text(
        "<Config><ApiKey>sonarrkey0123456789</ApiKey></Config>"
    )
    (appdata / "prowlarr" / "config.xml").write_text(
        "<Config><ApiKey>prowlarrkey12345</ApiKey></Config>"
    )
    conf = appdata / "unpackerr" / "unpackerr.conf"
    conf.write_text(UNPACKERR_CONF)
    conf.chmod(0o664)

    deleted: list[str] = []
    applications = [
        {
            "id": 1,
            "name": "Sonarr",
            "implementation": "Sonarr",
            "fields": [{"name": "baseUrl", "value": "http://sonarr:8989"}],
        },
        {
            "id": 2,
            "name": "Lidarr",
            "implementation": "Lidarr",
            "fields": [{"name": "baseUrl", "value": "http://lidarr:8686"}],
        },
    ]

    def transport(method, url, headers, body):
        assert "prowlarr" in url and headers["X-Api-Key"] == "prowlarrkey12345"
        if method == "GET" and url.endswith("/api/v1/applications"):
            return Response(200, json.dumps(applications).encode())
        if method == "DELETE":
            deleted.append(url.rsplit("/", 1)[1])
            return Response(200, b"")
        return Response(404, b"")

    services = Services(scratch_layout)
    services.app_setup_factory = lambda layout: AppSetup(
        layout, transport=transport, resolver=lambda name: name
    )
    monkeypatch.setattr(services.updates, "refresh", lambda **_: False)
    client = TestClient(create_app(services=services))

    plan = client.get("/api/apps/saltbox/lidarr/uninstall").json()
    assert plan["references"] == [
        {
            "app": "Unpackerr",
            "container": "unpackerr",
            "what": "the [[lidarr]] section for lidarr",
            "kind": "unpackerr",
        },
        {
            "app": "Prowlarr",
            "container": "prowlarr",
            "what": "the app Lidarr it syncs indexers to",
            "kind": "prowlarr",
        },
    ]
    # Even when the settings go too: the key was read before the folder was deleted.
    done = client.post("/api/apps/saltbox/lidarr/uninstall", json={"delete_data": True}).json()
    assert done["deleted"] == [str(appdata / "lidarr")] and done["not_cleaned"] == []
    assert done["cleaned"] == [
        "Unpackerr: removed the [[lidarr]] section for lidarr",
        "Prowlarr: removed the app Lidarr it syncs indexers to",
    ]
    text = conf.read_text()
    assert "[[lidarr]]" not in text and "[[sonarr]]" in text and "sonarrkey0123456789" in text
    assert conf.stat().st_mode & 0o777 == 0o664
    assert restarted == ["restart unpackerr"] and deleted == ["2"]

    # Sonarr is named the same way, but was not removed: nothing to say about it.
    assert client.get("/api/apps/saltbox/radarr/uninstall").json()["references"] == []
