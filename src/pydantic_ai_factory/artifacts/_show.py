"""How a person sees an artifact: as plain text, as Markdown, or as JSON.

The library writes the Markdown itself, from the value's own shape, so no station has
to. Every artifact is a value Pydantic can serialise, so every artifact can be shown.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from ._artifact import adapter_for


class OutputFormat(StrEnum):
    """How a person sees a station's output.

    ``TEXT`` shows a string as it was written, and anything else as JSON. ``MARKDOWN``
    shows a string as Markdown, and writes any other value as Markdown from its shape.
    ``JSON`` shows the value as formatted JSON.
    """

    TEXT = "text"
    MARKDOWN = "markdown"
    JSON = "json"


def format_for(output_type: Any) -> OutputFormat:
    """The format a station shows its output in when it names none.

    A string is text. Anything else is Markdown, written from the value's shape.
    """
    return OutputFormat.TEXT if output_type is str else OutputFormat.MARKDOWN


def show(value: Any, output_type: Any, output_format: OutputFormat) -> str:
    """Show ``value``, an ``output_type`` or the JSON data of one, in ``output_format``.

    Raises ``pydantic.ValidationError`` when ``value`` is not an ``output_type``.
    """
    adapter = adapter_for(output_type)
    loaded = adapter.validate_python(value)
    if isinstance(loaded, str) and output_format is not OutputFormat.JSON:
        return loaded
    if output_format is OutputFormat.MARKDOWN:
        return to_markdown(loaded)
    data = adapter.dump_python(loaded, mode="json")
    return json.dumps(data, indent=2, ensure_ascii=False)


def to_markdown(value: Any) -> str:
    """Write any value Pydantic can serialise as Markdown, from its shape.

    A model's ``title`` field, when it has one, becomes the heading. Each other field
    becomes a section headed by the field's title, or by its name in words. A string
    is a paragraph, a list a bullet list, an empty list "None.", and a nested model a
    section one level down. The same value always gives the same text.
    """
    lines: list[str] = []
    if isinstance(value, BaseModel):
        title = _title_of(value)
        if title:
            lines += [f"# {title}", ""]
        _model(value, 2, lines, skip="title" if title else None)
    else:
        _block(value, 2, lines)
    return "\n".join(lines).rstrip() + "\n"


def _title_of(value: Any) -> str | None:
    """A model's ``title`` field, when it has one that is a string with text in it."""
    if not isinstance(value, BaseModel) or "title" not in type(value).model_fields:
        return None
    title = getattr(value, "title", None)
    return title if isinstance(title, str) and title.strip() else None


def _heading(name: str, title: str | None) -> str:
    if title:
        return title
    words = name.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def _model(value: BaseModel, level: int, lines: list[str], skip: str | None = None) -> None:
    for name, field in type(value).model_fields.items():
        if name == skip:
            continue
        lines += [f"{'#' * min(level, 6)} {_heading(name, field.title)}", ""]
        _block(getattr(value, name), level + 1, lines)


def _block(value: Any, level: int, lines: list[str]) -> None:
    if isinstance(value, BaseModel):
        _model(value, level, lines)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        items: list[Any] = list(value)  # pyright: ignore[reportUnknownArgumentType]
        if not items:
            lines += ["None.", ""]
        elif any(isinstance(item, BaseModel) for item in items):
            for number, item in enumerate(items, start=1):
                title = _title_of(item)
                lines += [f"{'#' * min(level, 6)} {title or number}", ""]
                if isinstance(item, BaseModel):
                    _model(item, level + 1, lines, skip="title" if title else None)
                else:
                    lines += [_inline(item), ""]
        else:
            lines += [f"- {_inline(item)}" for item in items]
            lines.append("")
        return
    if isinstance(value, dict):
        entries: dict[Any, Any] = value  # pyright: ignore[reportUnknownVariableType]
        lines += [f"- **{key}**: {_inline(item)}" for key, item in entries.items()] or ["None."]
        lines.append("")
        return
    lines += [_inline(value), ""]


def _inline(value: Any) -> str:
    if value is None:
        return "None."
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, default=str)
