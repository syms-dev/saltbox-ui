"""A small JSON-over-HTTP client for talking to the apps' own APIs.

Requests go straight to the containers, at the address Docker gave them, never
through a proxy. The transport is a plain function so tests can replace the
network with in-memory fakes.
"""

from __future__ import annotations

import contextlib
import html
import http.client
import json
import re
import socket
import ssl
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

TIMEOUT = 20  # seconds without a byte arriving
DEADLINE = 60  # seconds for a whole answer, however slowly it trickles in
MAX_BODY_BYTES = 32 * 1024 * 1024  # a large library's full listing from Sonarr is a few MiB


class SetupError(RuntimeError):
    """An app could not be reached or refused a request."""


@dataclass
class Response:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        text = self.body.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        try:
            # Reject NaN, Infinity and 1e999: an app could otherwise put a value here that
            # the daemon cannot send on in its own JSON answer (which would 500 the whole
            # page). A non-finite number makes the answer parse as None, like any non-JSON.
            return json.loads(text, parse_constant=_not_finite, parse_float=_finite_float)
        except (ValueError, RecursionError):  # not JSON, non-finite, or nested absurdly deep
            return None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def _not_finite(_: str) -> float:
    raise ValueError("non-finite number in JSON")


def _finite_float(value: str) -> float:
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):  # NaN or ±inf
        raise ValueError("non-finite number in JSON")
    return number


Transport = Callable[[str, str, dict[str, str], bytes | None], Response]


REDIRECTS = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 3


class _Watchdog:
    """Ends a request that has taken too long, wherever it is stuck.

    A timeout on the socket only limits the wait for the next byte: an answer that
    trickles in, headers included, would never trip it. Shutting the socket down from
    a timer makes whatever read is in progress fail at once.
    """

    def __init__(self, connection: http.client.HTTPConnection, seconds: float):
        self.expired = False
        self._connection = connection
        self._timer = threading.Timer(seconds, self._expire)
        self._timer.daemon = True

    def _expire(self) -> None:
        self.expired = True
        sock = self._connection.sock
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

    def __enter__(self) -> _Watchdog:
        self._timer.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._timer.cancel()


def _once(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
    """One request, straight to the host: no proxy, no redirects, bounded in size and time."""
    parts = urllib.parse.urlsplit(url)
    host, port = parts.hostname, parts.port
    if not host:
        raise ValueError("no host")
    if parts.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            host, port, timeout=TIMEOUT, context=ssl.create_default_context()
        )
    else:
        connection = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    try:
        with _Watchdog(connection, DEADLINE) as watchdog:
            try:
                connection.request(method, target, body=body, headers=headers)
                response = connection.getresponse()
                chunks, total = [], 0
                while chunk := response.read1(65536):
                    total += len(chunk)
                    if total > MAX_BODY_BYTES:
                        raise OSError("the answer is too large")
                    chunks.append(chunk)
                if response.length:  # the connection ended before the body did
                    raise OSError("the answer was cut short")
            except (OSError, http.client.HTTPException) as error:
                if watchdog.expired:
                    raise TimeoutError("the answer took too long") from error
                raise
            if watchdog.expired:
                # Cut off mid-answer, in a place where what had arrived still parsed.
                raise TimeoutError("the answer took too long")
        return Response(response.status, b"".join(chunks), _headers(response.msg))
    finally:
        connection.close()


def _address(parts: urllib.parse.SplitResult) -> tuple[str, str, int | None]:
    return parts.scheme, (parts.hostname or "").lower(), parts.port


def fetch(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    *,
    follow: str = "same-address",
) -> Response:
    """Send a request and return the answer, following a redirect only where it is safe.

    What answers on an app's port is whatever runs in that container. A redirect of
    its choosing would send this (root) process, with the app's API key in the headers,
    to any host and port it names: services that only listen on the server itself, say.
    So a redirect is followed only to the same scheme, host and port (an app with a
    URL base redirects ``/api`` to ``/base/api``). ``follow="https"`` is for public
    registries, which hand a request on to another host: any https address is followed
    there, and the ``Authorization`` header is not taken along to a different host.
    Anything else comes back as the 3xx answer it is.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        origin = _address(parts)
    except ValueError as error:
        raise SetupError(f"bad address {url!r}: {error}") from error
    if parts.scheme not in ("http", "https"):
        raise SetupError(f"bad address {url!r}: not http or https")
    if (parts.hostname or "").endswith(".invalid"):
        name = (parts.hostname or "").split(".", 1)[0]
        raise SetupError(f"cannot reach {name}: its container has no address (is it running?)")
    try:
        for _ in range(MAX_REDIRECTS + 1):
            response = _once(method, url, headers, body)
            location = response.headers.get("location")
            if response.status not in REDIRECTS or not location:
                return response
            try:
                target = urllib.parse.urljoin(url, location)
                there = _address(urllib.parse.urlsplit(target))
            except ValueError:
                return response
            same = there == _address(urllib.parse.urlsplit(url))
            if not (same or (follow == "https" and there[0] == "https")):
                return response
            if there[1] != origin[1]:
                headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}
            if response.status in (301, 302, 303) and method not in ("GET", "HEAD"):
                method, body = "GET", None
            url = target
        return response
    except (OSError, http.client.HTTPException, ValueError) as error:
        # Refused, reset, timed out, or an answer that is not HTTP at all.
        reason = getattr(error, "reason", None) or str(error) or type(error).__name__
        host = urllib.parse.urlsplit(url).netloc
        raise SetupError(f"cannot reach {host}: {reason}") from error


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> Response:
    """The transport for the apps on this server (see ``fetch``)."""
    return fetch(method, url, headers, body)


def registry_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> Response:
    """The transport for public image registries, which redirect between https hosts."""
    return fetch(method, url, headers, body, follow="https")


def _headers(message: Any) -> dict[str, str]:
    """Lower-cased headers; repeated Set-Cookie headers are kept, one per line."""
    if message is None:
        return {}
    headers = {k.lower(): v for k, v in message.items()}
    cookies = message.get_all("Set-Cookie") if hasattr(message, "get_all") else None
    if cookies:
        headers["set-cookie"] = "\n".join(cookies)
    return headers


def describe_error(response: Response) -> str:
    """Turn an app's error body into one readable line."""
    data = response.json()
    messages: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                message = item.get("errorMessage") or item.get("message")
                if message:
                    messages.append(str(message))
    elif isinstance(data, dict):
        for key in ("message", "error", "errorMessage", "description"):
            if data.get(key):
                messages.append(str(data[key]))
                break
    if not messages and response.text.strip():
        messages.append(_plain(response.text)[:200])
    return "; ".join(messages) or f"HTTP {response.status}"


_HEAD = re.compile(r"<head\b.*?</\s*head\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]{0,1000}>")


def _plain(text: str) -> str:
    """An HTML error page as the words on it ("Bad Request - Invalid Hostname. HTTP Error
    400. ..."), so the first 200 characters say what went wrong, not the doctype."""
    stripped = text.strip()
    if not stripped.startswith("<"):
        return stripped
    words = _TAG.sub(" ", _HEAD.sub(" ", stripped[:20000]))
    words = html.unescape(" ".join(words.split()))
    return words or stripped


class Client:
    """JSON client for one app, with fixed headers such as an API key."""

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        transport: Transport | None = None,
        name: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Accept": "application/json", **(headers or {})}
        self.transport = transport or urllib_transport
        self.name = name or urllib.parse.urlsplit(self.base_url).hostname or self.base_url

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        form: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        ok: tuple[int, ...] = (200, 201, 202, 204),
    ) -> Response:
        url = self.base_url + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        all_headers = {**self.headers, **(headers or {})}
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body).encode()
            all_headers["Content-Type"] = "application/json"
        elif form is not None:
            body = urllib.parse.urlencode(form).encode()
            all_headers["Content-Type"] = "application/x-www-form-urlencoded"
        response = self.transport(method, url, all_headers, body)
        if response.status not in ok:
            raise SetupError(f"{self.name}: {describe_error(response)}")
        return response

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs).json()

    def post(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        return self.request("POST", path, json_body=body, **kwargs).json()

    def put(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        return self.request("PUT", path, json_body=body, **kwargs).json()
