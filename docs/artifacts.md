---
title: Artifacts and evidence
description: How a station's output is recorded, addressed by its content and kept unchanged.
---

# Artifacts and evidence

An artifact is the record of what one station hands to the next. It wraps a typed payload, is addressed by a digest of its content, and is never edited. This matters because everything that follows depends on it: a gate judges artifacts, a person approves one exact artifact, and a result can be traced back to what it was made from. This page shows how to create, store and correct artifacts.

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

The ref has two parts. The `type` names the kind of artifact; it defaults to the payload's class name. The `digest` is a SHA-256 hash of the whole record: the type, the payload and everything in the table below. Change any of it and the digest changes.

Always use `Artifact.create`. Calling `Artifact(...)` yourself would let you write a ref that does not match the content, and every store refuses such an artifact.

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
| `created_at` | When it was made. |

Together, `inputs` and `produced_by` give every artifact a lineage. You can walk from a final design back to the brief it started from.

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

All three derive from `FactoryError`.

| Error | Raised when |
|---|---|
| `ArtifactNotFound` | Nothing is filed under the ref you asked for. |
| `ArtifactCorrupt` | Content does not match its digest, or a stored file cannot be read. |
| `pydantic.ValidationError` | The stored payload does not fit the type you asked for. |

## Write your own store

A store is anything with these three async methods. `ArtifactStore` is a protocol, so no base class is needed.

```python test="skip"
class ArtifactStore(Protocol):
    async def put(self, artifact: Artifact[Any]) -> ArtifactRef: ...
    async def get(self, ref: ArtifactRef, payload_type: type[T]) -> Artifact[T]: ...
    async def exists(self, ref: ArtifactRef) -> bool: ...
```

This block shows the shape only, so it is not run as a sample. A store must check each artifact with `artifact.verify()` before keeping it.
