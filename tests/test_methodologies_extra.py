"""Elephant Goldfish sits behind the `methodologies` extra; the rest of the core does not.

Pydantic AI Harness is installed in this environment, so each test that needs it missing
runs in a fresh interpreter with an import hook that hides it.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

# Installed first in each fresh interpreter: any import of the harness fails as if it were
# not installed.
HIDE_THE_HARNESS = """
import sys
from importlib.abc import MetaPathFinder


class HideTheHarness(MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "pydantic_ai_harness" or name.startswith("pydantic_ai_harness."):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


sys.meta_path.insert(0, HideTheHarness())
"""

# Every name `pydantic_ai_factory.methodologies` exported in 0.1.
PUBLIC_IN_0_1 = (
    "Answer",
    "ElephantGoldfish",
    "Finding",
    "Goldfish",
    "GoldfishFailed",
    "GoldfishReport",
    "GoldfishResult",
    "Severity",
    "Stage",
    "Standard",
)


def _without_the_harness(code: str) -> subprocess.CompletedProcess[str]:
    script = HIDE_THE_HARNESS + textwrap.dedent(code)
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False
    )


def test_the_core_imports_without_the_extra() -> None:
    finished = _without_the_harness(
        """
        import pydantic_ai_factory
        from pydantic_ai_factory import (
            Factory, MemoryStore, Methodology, station, stations, stores, gates, artifacts,
        )

        print("pydantic_ai_harness" in sys.modules)
        """
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.strip() == "False"


def test_a_factory_runs_without_the_extra() -> None:
    finished = _without_the_harness(
        """
        from dataclasses import dataclass

        from pydantic_ai import Agent
        from pydantic_ai.models.test import TestModel

        from pydantic_ai_factory import (
            Factory, MemoryStore, Methodology, Verdict, failed, passed, station,
        )


        @dataclass
        class Brief(Methodology):
            name = "brief"
            version = "1"

            def get_instructions(self):
                return "Answer in one line."

            def judge(self, output, evidence) -> Verdict:
                return passed("one line") if "\\n" not in output.payload else failed("lines")


        writer = Agent(TestModel(), name="writer", output_type=str, capabilities=[Brief()])
        run = Factory("plain", [station(writer)]).run_sync("Say hello.", store=MemoryStore())

        print(run.outcome, "pydantic_ai_harness" in sys.modules)
        """
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.strip() == "passed False"


def test_methodologies_names_the_extra_when_it_is_missing() -> None:
    finished = _without_the_harness(
        """
        try:
            import pydantic_ai_factory.methodologies
        except ImportError as error:
            print(type(error).__name__, error.__cause__, error.__suppress_context__)
            print(error)
        """
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.splitlines() == [
        "ImportError None True",
        "Elephant Goldfish needs the methodologies extra: "
        'pip install "pydantic-ai-factory[methodologies]"',
    ]


def test_public_names_are_unchanged() -> None:
    import pydantic_ai_factory.methodologies as methodologies

    for name in PUBLIC_IN_0_1:
        assert getattr(methodologies, name) is not None, name
    assert set(PUBLIC_IN_0_1) <= set(methodologies.__all__)
