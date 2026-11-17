"""Whether a newer image is published for each running container.

For every container the image reference it was created from (``Config.Image``,
such as ``ghcr.io/hotio/sonarr:release``) is asked of its registry: a HEAD on
the tag's manifest returns the digest the tag points at now. The local image's
``RepoDigests`` hold the digest it was pulled at. When the registry's digest is
not among them, a newer image exists. HEAD requests do not count against
Docker Hub's pull limits, and the anonymous token flow covers Docker Hub, GHCR,
lscr.io and other registries that follow the distribution spec.

Saltbox updates an app by running its tag again, which pulls the image and
recreates the container; the daemon offers that as a job.
"""

from __future__ import annotations

import contextlib
import json
import platform
import re
import shutil
import subprocess
import time
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from ..appsetup.http import Response, SetupError, Transport, registry_transport

MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
    ]
)
CHALLENGE_RE = re.compile(r'(\w+)="([^"]*)"')

Runner = Callable[[list[str]], tuple[int, str]]
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_~-]{0,63}")
BUILD_VERSION = re.compile(r"version:-\s*(\S+)")


def host_arch() -> str:
    """This server's architecture as image indexes name it (amd64, arm64)."""
    machine = platform.machine().lower()
    return {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "arm"}.get(machine, machine)


def version_of(labels: dict[str, Any]) -> str:
    """The version an image says it is: the OCI label, else linuxserver's build_version."""
    found = str(labels.get("org.opencontainers.image.version") or "").strip()
    if not found:
        match = BUILD_VERSION.search(str(labels.get("build_version") or ""))
        found = match.group(1) if match else ""
    # The text comes from an image's publisher: only a plain version string is kept.
    return found if VERSION.fullmatch(found) else ""


@dataclass
class ImageRef:
    registry: str  # host to talk to
    repository: str
    tag: str

    @property
    def manifest_url(self) -> str:
        return f"https://{self.registry}/v2/{self.repository}/manifests/{self.tag}"


def parse_ref(image: str) -> ImageRef | None:
    """``sonarr``, ``hotio/sonarr:release`` or ``ghcr.io/hotio/sonarr:release``.

    Images pinned to a digest are skipped: they never change.
    """
    if not image or "@" in image:
        return None
    name, tag = image, "latest"
    last = image.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = image.rsplit(":", 1)
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, repository = first, rest
    else:
        registry, repository = "docker.io", name
    if registry in ("docker.io", "index.docker.io"):
        registry = "registry-1.docker.io"
        if "/" not in repository:
            repository = f"library/{repository}"
    return ImageRef(registry, repository, tag)


class Registry:
    def __init__(self, transport: Transport | None = None):
        self.transport = transport or registry_transport
        self._tokens: dict[str, str] = {}

    def _head(self, ref: ImageRef, token: str = "") -> Response:
        headers = {"Accept": MANIFEST_TYPES}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self.transport("HEAD", ref.manifest_url, headers, None)

    def _token(self, challenge: str, ref: ImageRef) -> str:
        if len(challenge) > 8192:  # a real WWW-Authenticate is short; a long one is quadratic here
            raise SetupError(f"{ref.registry}: its authentication challenge is too long")
        params = dict(CHALLENGE_RE.findall(challenge))
        realm = params.pop("realm", "")
        if not realm:
            raise SetupError(f"{ref.registry}: no token service in its challenge")
        # The address comes from the registry's answer: only an https token service is asked.
        if not realm.startswith("https://") or "#" in realm:
            raise SetupError(f"{ref.registry}: its token service is not an https address")
        params.setdefault("scope", f"repository:{ref.repository}:pull")
        key = f"{realm}|{params.get('scope')}"
        if key not in self._tokens:
            url = f"{realm}?{urllib.parse.urlencode(params)}"
            response = self.transport("GET", url, {"Accept": "application/json"}, None)
            data = response.json() if response.status == 200 else None
            if not isinstance(data, dict):  # a list, string or number has no token to read
                data = {}
            token = data.get("token") or data.get("access_token")
            if not token:
                raise SetupError(
                    f"{ref.registry}: no anonymous pull token (HTTP {response.status})"
                )
            self._tokens[key] = str(token)
        return self._tokens[key]

    def _get(self, ref: ImageRef, url: str, accept: str) -> Response:
        """A GET with the same anonymous token as the HEAD, asked for if the registry wants it."""
        response = self.transport("GET", url, {"Accept": accept}, None)
        if response.status == 401:
            token = self._token(response.headers.get("www-authenticate", ""), ref)
            headers = {"Accept": accept, "Authorization": f"Bearer {token}"}
            response = self.transport("GET", url, headers, None)
        if response.status != 200:
            raise SetupError(f"{ref.registry}/{ref.repository}: HTTP {response.status}")
        return response

    def published_version(self, ref: ImageRef, arch: str | None = None) -> str:
        """The version the tag now points at, from the published image's labels.

        The manifest names the image's config, whose labels carry the version
        (``org.opencontainers.image.version``). A tag for several platforms is an index:
        the entry for this server's architecture is read. Only asked for images that have
        an update, to show what it brings; "" when the image does not say.
        """
        arch = arch or host_arch()
        base = f"https://{ref.registry}/v2/{ref.repository}"
        manifest = self._get(ref, ref.manifest_url, MANIFEST_TYPES).json()
        if isinstance(manifest, dict) and isinstance(manifest.get("manifests"), list):
            entry = next(
                (
                    m
                    for m in manifest["manifests"]
                    if isinstance(m, dict)
                    and isinstance(m.get("platform"), dict)
                    and m["platform"].get("os") == "linux"
                    and m["platform"].get("architecture") == arch
                ),
                None,
            )
            digest = str((entry or {}).get("digest", ""))
            if not DIGEST.fullmatch(digest):
                return ""
            manifest = self._get(ref, f"{base}/manifests/{digest}", MANIFEST_TYPES).json()
        config = manifest.get("config") if isinstance(manifest, dict) else None
        digest = str(config.get("digest", "")) if isinstance(config, dict) else ""
        if not DIGEST.fullmatch(digest):
            return ""
        blob = self._get(ref, f"{base}/blobs/{digest}", "application/json").json()
        settings = blob.get("config") if isinstance(blob, dict) else None
        labels = settings.get("Labels") if isinstance(settings, dict) else None
        return version_of(labels if isinstance(labels, dict) else {})

    def digest(self, ref: ImageRef) -> str:
        response = self._head(ref)
        if response.status == 401:
            challenge = response.headers.get("www-authenticate", "")
            response = self._head(ref, self._token(challenge, ref))
        if response.status != 200:
            raise SetupError(f"{ref.registry}/{ref.repository}:{ref.tag}: HTTP {response.status}")
        digest = response.headers.get("docker-content-digest", "")
        if not digest:
            raise SetupError(f"{ref.registry}: no digest in the reply")
        return digest


def _docker(args: list[str]) -> tuple[int, str]:
    if shutil.which("docker") is None:
        return 127, "docker not found"
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, str(error)
    return result.returncode, result.stdout if result.returncode == 0 else result.stderr


def local_digests(image_ids: Iterable[str], runner: Runner | None = None) -> dict[str, list[str]]:
    """``RepoDigests`` per local image id (the sha256 digests it was pulled at)."""
    ids = sorted({i for i in image_ids if i})
    if not ids:
        return {}
    code, output = (runner or _docker)(["docker", "image", "inspect", *ids])
    if code != 0:
        raise SetupError(output.strip() or "docker image inspect failed")
    result: dict[str, list[str]] = {}
    for item in json.loads(output or "[]"):
        digests = [d.split("@", 1)[1] for d in item.get("RepoDigests") or [] if "@" in d]
        result[str(item.get("Id", ""))] = digests
    return result


@dataclass
class ImageStatus:
    image: str
    status: str  # "current", "update", "unknown"
    detail: str = ""
    remote_digest: str = ""
    containers: list[str] = field(default_factory=list)
    # What the running image says it is, and what the published one says (updates only).
    local_version: str = ""
    remote_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_updates(
    containers: list[dict[str, Any]],
    *,
    registry: Registry | None = None,
    runner: Runner | None = None,
) -> dict[str, ImageStatus]:
    """Update status per image reference, for the given container records."""
    registry = registry or Registry()
    by_image: dict[str, ImageStatus] = {}
    image_ids: dict[str, str] = {}
    for container in containers:
        ref = container.get("image_ref") or container.get("image") or ""
        if not ref:
            continue
        status = by_image.setdefault(ref, ImageStatus(ref, "unknown"))
        status.containers.append(container["name"])
        if not status.local_version:
            labels = container.get("labels")
            status.local_version = version_of(labels if isinstance(labels, dict) else {})
        image_ids.setdefault(ref, str(container.get("image_id", "")))
    try:
        local = local_digests(image_ids.values(), runner)
    except (SetupError, ValueError) as error:
        for status in by_image.values():
            status.detail = f"cannot read local images: {error}"
        return by_image
    for ref, status in by_image.items():
        parsed = parse_ref(ref)
        if parsed is None:
            status.detail = "pinned to a digest"
            continue
        try:
            remote = registry.digest(parsed)
        except SetupError as error:
            status.detail = str(error)
            continue
        status.remote_digest = remote
        have = local.get(image_ids.get(ref, ""), [])
        if not have:
            status.detail = "built locally or pulled without a digest"
        elif remote in have:
            status.status = "current"
        else:
            status.status = "update"
            status.detail = "a newer image is published"
            # The update stands either way; a registry that will not say only hides the version.
            with contextlib.suppress(SetupError, ValueError):
                status.remote_version = registry.published_version(parsed)
    return by_image


def now() -> float:
    return time.time()
