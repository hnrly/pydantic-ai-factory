"""The one file that knows which factory this app runs. Change it to run your own.

The rest of the app works with any factory. It needs two things from here:

- ``build_factory(model)`` returns the factory. The worker calls it with the model it
  runs on. The web process calls it with a scripted model, only to read its digest.
- ``render_output(run, store)`` returns the Markdown a run's page shows for the work
  the run waits on or produced, or ``None`` when there is nothing to show.

Here the factory is the software factory, and its work is a ``Design``.
"""

from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai_factory import ArtifactRef, Factory, Run, to_markdown
from pydantic_ai_factory.factory import Store
from pydantic_ai_factory.methodologies import GoldfishReport
from pydantic_ai_factory_software import Design, SoftwareFactory


def build_factory(model: Model | str) -> Factory:
    """Return the factory this app runs, on ``model``."""
    return SoftwareFactory(model)


async def render_output(run: Run, store: Store) -> str | None:
    """Return the design the run waits on or produced, as Markdown, and how it was reviewed."""
    waiting = run.waiting
    ref = run.output_ref or (waiting.artifact if waiting else None)
    if ref is None or ref.type != "Design":
        return None
    design = (await store.get(ref, Design)).payload
    text = to_markdown(design).rstrip()
    review = await _review_line(run, ref, store)
    return f"{text}\n\n{review}\n" if review else f"{text}\n"


async def _review_line(run: Run, ref: ArtifactRef, store: Store) -> str | None:
    """One line on how the design was reviewed, from its last round of goldfish reports."""
    step = next((step for step in reversed(run.steps) if step.output_ref == ref), None)
    reports = [
        (await store.get(evidence, GoldfishReport)).payload
        for evidence in (step.evidence if step else ())
        if evidence.type == "GoldfishReport"
    ]
    if not reports:
        return None
    final = max(reports, key=lambda report: report.round)
    rounds = "1 round" if final.round == 1 else f"{final.round} rounds"
    left = len(final.open)
    ending = (
        "the last round was clean."
        if final.clean
        else f"{left} finding{'' if left == 1 else 's'} {'is' if left == 1 else 'are'} still open."
    )
    return (
        f"*Reviewed in {rounds} by {len(final.goldfish)} goldfish, under the Elephant "
        f"Goldfish method; {ending}*"
    )
