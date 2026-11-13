---
title: Artifacts and evidence
description: How a station's output is recorded, addressed by its content and kept unchanged.
---

# Artifacts and evidence

An artifact is the record of what one station hands to the next. It wraps a typed payload, is addressed by a digest of its content, and is never edited: a correction is a new artifact. This matters because everything that follows depends on it: a gate judges artifacts, a person approves one exact artifact, and a result can be traced back to what it was made from. This page shows how to create, store and correct artifacts.

Evidence is not a separate type. An artifact is called evidence when a gate reads it. A review report and a design are the same kind of record.

## Create an artifact

The payload is any Pydantic model. `Artifact.create` records it and computes its ref.

```python
from pydantic import BaseModel

from pydantic_ai_factory import Artifact


class Design(BaseModel):
    summary: str
    steps: list[str]


design = Artifact.create(Design(summary="Split the bill evenly", steps=["add", "divide"]))

assert design.ref.type == "Design"
assert design.ref.digest.startswith("sha256:")
assert design.payload.summary == "Split the bill evenly"
```

The ref has two parts. The `type` names the kind of artifact; it defaults to the payload's class name. The `digest` is a SHA-256 hash of the whole record: the type, the payload and everything in the table below, including the time it was made. Change any of it and the digest changes. So two calls to `create` with the same payload give two different artifacts, unless you pass the same `created_at`.

The type is a label you choose. If two packages both have a `Design` model, pass `type=` to tell their artifacts apart.

Always use `Artifact.create`. Calling `Artifact(...)` yourself would let you write a ref that does not match the content, and every store refuses such an artifact.

`create` copies your payload. Changing your own object afterwards does not change the artifact.

## What an artifact records

| Field | Meaning |
|---|---|
| `ref` | The type and digest. This is how the artifact is named everywhere else. |
| `payload` | Your typed data. |
| `produced_by` | The station, its version, the run and the attempt that made it. |
| `inputs` | The refs of the artifacts it was made from. |
| `supersedes` | The ref of the artifact it corrects, if any. |
| `mission_id` | The mission it belongs to. |
| `methodologies` | The name and version of each methodology in force when it was made. |
| `created_at` | When it was made, in UTC. |
| `schema_version` | The version of this record's own layout. |

Together, `inputs` and `produced_by` give every artifact a lineage. You can walk from a final design back to the brief it started from, as [reading without the payload class](#read-without-the-payload-class) shows.

```python
from pydantic import BaseModel

from pydantic_ai_factory import Artifact, MethodologyRef, Producer


class Brief(BaseModel):
    goal: str


class Design(BaseModel):
    summary: str


brief = Artifact.create(Brief(goal="Split a bill between friends"))

design = Artifact.create(
    Design(summary="Split the bill evenly"),
    produced_by=Producer(station="design", run_id="run-1"),
    inputs=[brief.ref],
    methodologies=[MethodologyRef(name="elephant-goldfish", version="1")],
)

assert design.inputs == (brief.ref,)
assert str(design.methodologies[0]) == "elephant-goldfish@1"
```

You will rarely fill these fields in by hand. Once stations and lines arrive, the runner does it for you.

## Correct an artifact

An artifact cannot be changed. To correct one, supersede it. That makes a new artifact which names the old one.

```python
from pydantic import BaseModel

from pydantic_ai_factory import Artifact


class Design(BaseModel):
    summary: str


first = Artifact.create(Design(summary="Split the bill evenly"))
second = first.supersede(Design(summary="Split the bill evenly, rounding to pennies"))

assert second.supersedes == first.ref
assert second.ref != first.ref
assert first.payload.summary == "Split the bill evenly"
```

Both artifacts still exist. This is what later makes an approval safe: a person approves one digest, and a corrected artifact has a different one.

The correction keeps the first artifact's type, inputs, mission and methodologies. It does not keep the producer, because a correction is often made by someone else. Pass `produced_by=` to say who made it.

## Store artifacts

A store keeps artifacts by digest. Two are included.

| Store | Keeps artifacts | Use it for |
|---|---|---|
| `MemoryStore` | In memory | Tests and short-lived runs. |
| `LocalStore` | As one JSON file each, under a folder you choose | Real runs on one machine. |

Reading an artifact back needs the payload type, so that you get a typed object and not a dictionary.

```python
import asyncio
import tempfile

from pydantic import BaseModel

from pydantic_ai_factory import Artifact, LocalStore


class Design(BaseModel):
    summary: str


async def main() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = LocalStore(folder)
        design = Artifact.create(Design(summary="Split the bill evenly"))

        ref = await store.put(design)
        loaded = await store.get(ref, Design)

        assert loaded.payload == design.payload
        assert await store.exists(ref)


asyncio.run(main())
```

A store is write-once. Putting the same artifact twice is harmless, and nothing in a store is ever updated or deleted by the library.

`LocalStore` names each file by its digest, under `artifacts/` in the folder you give it. A payload can never choose its own path. Each file is written under a temporary name and then moved into place, so a crash cannot leave half an artifact behind.

## Read without the payload class

Some code handles any artifact: a lineage walker, a report, a viewer. It may not have the payload's model. Ask for `Untyped` and the fields arrive in `model_extra`.

```python
import asyncio

from pydantic import BaseModel

from pydantic_ai_factory import Artifact, MemoryStore, Untyped


class Brief(BaseModel):
    goal: str


class Design(BaseModel):
    summary: str


async def main() -> None:
    store = MemoryStore()
    brief = Artifact.create(Brief(goal="Split a bill between friends"))
    design = Artifact.create(Design(summary="Split the bill evenly"), inputs=[brief.ref])
    await store.put(brief)
    await store.put(design)

    for ref in design.inputs:
        parent = await store.get(ref, Untyped)
        print(ref.type, parent.payload.model_extra)
        # > Brief {'goal': 'Split a bill between friends'}


asyncio.run(main())
```

## Which payloads are safe

The digest is taken from the payload as JSON, and a store keeps that JSON. So a payload must read back from its own JSON as the same data. `create` checks this and raises `ArtifactInvalid` if it does not hold. These are refused:

| Payload shape | Why |
|---|---|
| A field with an alias | The JSON is written by field name and would not read back. |
| A field with `exclude=True` | Its value would be silently lost. |
| A computed field on a model that forbids extra fields | The JSON would not read back. |
| A field typed as a base class that holds a subclass | The subclass's extra fields would be lost. |
| A dictionary whose keys collide as strings, such as `1` and `"1"` | One entry would be lost. |
| `NaN` or infinity | They are not valid JSON. |

Strict models, dates, UUIDs, decimals, enums, tuples and nested models all work.

One shape is accepted but unwise: a `set` field. Its JSON order can differ between processes, so equal payloads can get different digests. Use a sorted list.

Treat `artifact.payload` as read-only, and prefer frozen payload models. If the payload object is changed in place, `artifact.verify()` raises `ArtifactCorrupt` and a store refuses the artifact.

## When a payload model changes

Models evolve. An artifact stored last month still reads back after you add a field with a default, or remove a field: the new model is a view of the stored JSON. The stored JSON, the digest and any approval bound to it are unchanged, and the artifact still passes `verify()`.

A change the stored JSON cannot satisfy, such as a new required field, raises `pydantic.ValidationError` on read. Read it with `Untyped` instead.

## Integrity

Every store checks an artifact against its digest twice: when it goes in and when it comes out. If a file was changed on disk, reading it raises `ArtifactCorrupt`. It never returns the changed content.

```python
import asyncio

from pydantic import BaseModel

from pydantic_ai_factory import Artifact, ArtifactCorrupt, MemoryStore


class Design(BaseModel):
    summary: str


async def main() -> None:
    honest = Artifact.create(Design(summary="Split the bill evenly"))
    forged = honest.model_copy(update={"payload": Design(summary="Something else")})

    try:
        await MemoryStore().put(forged)
    except ArtifactCorrupt as error:
        print(type(error).__name__)
        # > ArtifactCorrupt


asyncio.run(main())
```

## Errors

The first three derive from `FactoryError`.

| Error | Raised when |
|---|---|
| `ArtifactNotFound` | Nothing is filed under the ref you asked for. |
| `ArtifactCorrupt` | Content does not match its digest, or a stored file cannot be read. |
| `ArtifactInvalid` | `create` was given a payload or a detail it cannot record. |
| `pydantic.ValidationError` | The stored payload does not fit the type you asked for. |

## Write your own store

A store is anything with three async methods: `put`, `get` and `exists`. `ArtifactStore` is a protocol, so no base class is needed. Two functions do the integrity checks for you. `encode_artifact` checks an artifact and returns the bytes to keep. `decode_artifact` checks stored bytes against a ref and returns the artifact.

```python
import asyncio
from typing import Any, TypeVar

from pydantic import BaseModel

from pydantic_ai_factory import (
    Artifact,
    ArtifactNotFound,
    ArtifactRef,
    decode_artifact,
    encode_artifact,
)

T = TypeVar("T", bound=BaseModel)


class DictStore:
    def __init__(self) -> None:
        self.rows: dict[str, bytes] = {}

    async def put(self, artifact: Artifact[Any]) -> ArtifactRef:
        self.rows[artifact.ref.digest] = encode_artifact(artifact)
        return artifact.ref

    async def get(self, ref: ArtifactRef, payload_type: type[T]) -> Artifact[T]:
        if ref.digest not in self.rows:
            raise ArtifactNotFound(f"no artifact is filed under {ref}")
        return decode_artifact(ref, self.rows[ref.digest], payload_type)

    async def exists(self, ref: ArtifactRef) -> bool:
        return ref.digest in self.rows


class Design(BaseModel):
    summary: str


async def main() -> None:
    store = DictStore()
    ref = await store.put(Artifact.create(Design(summary="Split the bill evenly")))
    print((await store.get(ref, Design)).payload)
    # > summary='Split the bill evenly'


asyncio.run(main())
```

Key a store by the digest alone. `decode_artifact` then reports a ref with the right digest and the wrong type as corrupt.
