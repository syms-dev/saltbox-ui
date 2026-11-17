"""Text widgets that show text as it is.

Textual reads square brackets in a widget's text as markup: ``[bold]`` restyles,
``[/b]`` out of place is an error that ends the app, and ``[@click=...]`` makes
a link that runs an action when clicked. Nearly everything this installer shows
comes from somewhere else (an app's error message, rclone's output, folder
names in cloud storage, what the person typed), and it runs as root. So no
widget here reads markup: brackets are shown, never obeyed.
"""

from __future__ import annotations

import re
from typing import Any

from textual.app import App
from textual.content import Content
from textual.widgets import Label as _Label
from textual.widgets import RadioButton as _RadioButton
from textual.widgets import Static as _Static

# Escape sequences a terminal obeys (repaint the screen, set the title, write the
# clipboard) and other control bytes. An app's error message, rclone's output and cloud
# folder names reach these widgets and run as root's terminal, so they are stripped, the
# same as ``sbupdate.clean_line`` does for command output. Tab and newline are kept.
_ESCAPES = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[PX^_][^\x1b]*\x1b\\"
    r"|\x1b[()][0-9A-Za-z]"
    r"|\x1b[@-Z\\-_=>]"
)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def scrub(text: object) -> str:
    """``text`` with terminal escape sequences and control bytes removed."""
    return _CONTROL.sub("", _ESCAPES.sub("", str(text)))


def literal(text: object) -> Content:
    """``text`` as content that is shown exactly, for widgets that take a label."""
    return Content(scrub(text))


class Static(_Static):
    def __init__(self, content: Any = "", **kwargs: Any) -> None:
        kwargs["markup"] = False
        super().__init__(scrub(content) if isinstance(content, str) else content, **kwargs)

    def update(self, content: Any = "") -> None:
        super().update(scrub(content) if isinstance(content, str) else content)


class Label(_Label):
    def __init__(self, content: Any = "", **kwargs: Any) -> None:
        kwargs["markup"] = False
        super().__init__(scrub(content) if isinstance(content, str) else content, **kwargs)


class RadioButton(_RadioButton):
    def __init__(self, label: Any = "", *args: Any, **kwargs: Any) -> None:
        super().__init__(literal(label), *args, **kwargs)


class PlainApp(App[None]):
    """An app whose notifications show their text as it is."""

    def notify(self, message: str, **kwargs: Any) -> None:  # type: ignore[override]
        kwargs["markup"] = False
        super().notify(scrub(message), **kwargs)
