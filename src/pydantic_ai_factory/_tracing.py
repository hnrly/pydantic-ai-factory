"""Tracing: OpenTelemetry spans for runs, station attempts, agent runs, gates and answers.

This is a side module that any layer may use. It depends only on the OpenTelemetry API,
which does nothing until the application installs a tracer provider. So tracing costs
nothing when it is off, and the library never chooses where traces go.

Every attribute is named under ``factory.``. Artifacts are recorded as digests, never as
content, and an attribute with no value is left off.
"""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

__all__ = ["TRACER_NAME", "Span", "annotate", "mark_error", "span"]

TRACER_NAME = "pydantic_ai_factory"


def annotate(span: Span, attributes: Mapping[str, object]) -> None:
    """Set ``attributes`` on ``span``, leaving off any that are ``None`` or empty.

    A value that is a sequence is recorded as a list of strings. Anything else that is
    not a string, a number or a boolean is recorded as its string.
    """
    if not span.is_recording():
        return
    for name, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, str | bool | int | float):
            span.set_attribute(name, value)
        elif isinstance(value, Sequence):
            items = [str(item) for item in value]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            if items:
                span.set_attribute(name, items)
        else:
            span.set_attribute(name, str(value))


def mark_error(span: Span, message: str) -> None:
    """Mark ``span`` as an error, with a plain message saying what could not be done."""
    span.set_status(Status(StatusCode.ERROR, message))


@contextmanager
def span(title: str, attributes: Mapping[str, object] | None = None) -> Generator[Span]:
    """Open a span as the current one, so spans made inside it nest under it.

    An exception that escapes is recorded and marks the span as an error. A
    ``BaseException`` that is not an ``Exception``, such as cancellation or a station
    stopping to wait for a person, does not.
    """
    tracer = trace.get_tracer(TRACER_NAME)
    with tracer.start_as_current_span(
        title, record_exception=False, set_status_on_exception=False
    ) as current:
        annotate(current, attributes or {})
        try:
            yield current
        except Exception as error:
            current.record_exception(error)
            mark_error(current, f"{type(error).__name__}: {error}")
            raise
