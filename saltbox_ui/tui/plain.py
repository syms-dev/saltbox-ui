"""Text widgets that show text as it is.

Textual reads square brackets in a widget's text as markup: ``[bold]`` restyles,
``[/b]`` out of place is an error that ends the app, and ``[@click=...]`` makes
a link that runs an action when clicked. Nearly everything this installer shows
comes from somewhere else (an app's error message, rclone's output, folder
names in cloud storage, what the person typed), and it runs as root. So no
widget here reads markup: brackets are shown, never obeyed.
"""

from __future__ import annotations

from typing import Any

from textual.app import App
from textual.content import Content
from textual.widgets import Label as _Label
from textual.widgets import RadioButton as _RadioButton
from textual.widgets import Static as _Static


def literal(text: object) -> Content:
    """``text`` as content that is shown exactly, for widgets that take a label."""
    return Content(str(text))


class Static(_Static):
    def __init__(self, content: Any = "", **kwargs: Any) -> None:
        kwargs["markup"] = False
        super().__init__(content, **kwargs)


class Label(_Label):
    def __init__(self, content: Any = "", **kwargs: Any) -> None:
        kwargs["markup"] = False
        super().__init__(content, **kwargs)


class RadioButton(_RadioButton):
    def __init__(self, label: Any = "", *args: Any, **kwargs: Any) -> None:
        super().__init__(literal(label), *args, **kwargs)


class PlainApp(App[None]):
    """An app whose notifications show their text as it is."""

    def notify(self, message: str, **kwargs: Any) -> None:  # type: ignore[override]
        kwargs["markup"] = False
        super().notify(message, **kwargs)
