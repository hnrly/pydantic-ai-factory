"""The lowest layer: shared record shapes, names, digests and the base error.

Everything else in the library is built from these. This module imports nothing from
the rest of the package.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

__all__ = [
    "Digest",
    "FactoryError",
    "MethodologyRef",
    "Name",
    "Record",
    "canonical_json",
    "digest_of",
]

Name = Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=r"^\S(.*\S)?$")]
"""A short label with no leading or trailing space, such as a station or type name."""

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
"""A SHA-256 content digest, written ``sha256:`` followed by 64 hexadecimal digits."""


class FactoryError(Exception):
    """Base class for every error this library raises on purpose."""


class Record(BaseModel):
    """A value that crosses a boundary: frozen, and strict about unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class MethodologyRef(Record):
    """The name and version of a methodology, as stamped on a record."""

    name: Name
    version: Name

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


def canonical_json(value: Any) -> bytes:
    """Encode JSON-ready data as bytes that are the same on every machine.

    Keys are sorted and no spare whitespace is written, so equal data always gives
    equal bytes. ``NaN`` and infinity are refused because they are not valid JSON.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest_of(value: Any) -> str:
    """Return the SHA-256 digest of JSON-ready data in its canonical form."""
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()
