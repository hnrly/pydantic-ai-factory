"""Answers as a worker reads them off a queue: built from JSON, not by the run's own verbs.

``run.reply`` refuses a bad reply before it is made, so a test of what ``Factory.check``
and ``Factory.resume`` refuse builds the reply here, as a queue would hand it over.
"""

from __future__ import annotations

from pydantic_ai import DeferredToolResults

from pydantic_ai_factory import Reply, Run

_ABOUT = {"run_id", "revision", "station", "waiting", "work", "by", "at"}


def queued_reply(run: Run, results: DeferredToolResults | None = None) -> Reply:
    """A reply to ``run`` carrying ``results`` as given, with none of ``run.reply``'s checks."""
    about = run.approve(by="ada").model_dump(include=_ABOUT)
    return Reply.model_validate({**about, "results": results or DeferredToolResults()})
