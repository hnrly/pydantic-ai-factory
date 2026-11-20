"""The acceptance test for one row of the roadmap's "Today" table.

It lives here, not in the library's own ``tests/test_acceptance.py``, because it proves
the row through the example as an operator runs it: ``board.py``, its web process and a
worker, sharing one SQLite file.
"""

from __future__ import annotations

from pydantic_ai_factory.serve import work
from pydantic_ai_factory_software import SoftwareFactory, offline_model

from tests.conftest import Served
from tests.support import answer, column, run, start


def test_you_can_serve_a_factory_as_a_board_with_its_work_done_by_a_separate_worker(
    served: Served,
) -> None:
    client = served.client
    run_id = start(client)
    assert column(client, run_id) == "design"
    assert run(client, run_id)["state"] == "queued"

    work(SoftwareFactory, served.store, served.dispatcher, model=offline_model(), until_idle=True)
    waiting = run(client, run_id)
    assert (waiting["state"], waiting["actions"]) == ("waiting", ["approve", "changes", "reject"])
    assert waiting["output"].startswith("# To-do list")

    assert answer(client, run_id, "approve") == 202
    work(SoftwareFactory, served.store, served.dispatcher, model=offline_model(), until_idle=True)

    assert run(client, run_id)["state"] == "passed"
    assert column(client, run_id) == "done"
