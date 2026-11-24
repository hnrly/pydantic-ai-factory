"""A run's track shows every station in order, with where the work is and which attempt."""

from __future__ import annotations

from pydantic_ai_factory import Factory, SQLiteStore
from pydantic_ai_factory.serve._board.history import track
from pydantic_ai_factory.serve._board.views import TrackStop

from .support import MISSION, running_run, wait

STATIONS = ["draft", "review"]


def states(stops: list[TrackStop]) -> list[tuple[str, str, int | None]]:
    return [(stop.station, stop.state, stop.attempt) for stop in stops]


def test_a_mission_not_yet_saved_has_every_station_ahead() -> None:
    assert states(track(STATIONS, None, "queued")) == [
        ("draft", "ahead", None),
        ("review", "ahead", None),
    ]


def test_a_live_run_is_here_at_its_station_and_a_stalled_one_is_stopped_there(
    factory: Factory, store: SQLiteStore
) -> None:
    run = wait(factory.run(MISSION, store=store, run_id="run-1"))
    assert run.station == "draft"

    assert states(track(STATIONS, run, "waiting")) == [
        ("draft", "here", 1),
        ("review", "ahead", None),
    ]
    assert states(track(STATIONS, run, "stalled"))[0] == ("draft", "stopped", 1)


def test_a_passed_run_has_every_station_done_with_the_attempt_it_ended_on(
    factory: Factory, store: SQLiteStore
) -> None:
    run = running_run(factory, store).model_copy(update={"outcome": "passed", "finished": True})

    assert [stop.state for stop in track(STATIONS, run, "passed")] == ["done", "done"]


def test_a_run_stopped_at_a_station_the_shape_no_longer_has_shows_only_what_it_tried(
    factory: Factory, store: SQLiteStore
) -> None:
    run = running_run(factory, store).model_copy(update={"station": "gone", "stopped_at": None})
    tried = {step.station for step in run.steps}

    assert [stop.state for stop in track(STATIONS, run, "stalled")] == [
        "done" if name in tried else "ahead" for name in STATIONS
    ]
