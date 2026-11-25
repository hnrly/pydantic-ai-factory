"""Measure factories and stations: a generic evaluation framework on Pydantic Evals.

A suite runs systems under test, called arms, on cases whose requests leave facts unsaid.
Each arm is run through one port contract: it gets a brief, may ask a simulated
stakeholder who holds the case's hidden facts, and delivers its work as text with a
status. Plain code grades each run from what crossed the ports; a judge model reads a
checklist beside the grade, and never decides it.

An arm can be a whole factory, one station, or a program run outside this process. The
framework knows nothing of what is delivered: a suite says that, with its cases, its fact
sheets and its words.

Install it with ``pip install "pydantic-ai-factory[evals]"``.
"""

from ._checklist import ChecklistJudge, Coverage, ItemVerdict, coverage, scripted_judge
from ._contract import (
    MAX_QUESTIONS_PER_CALL,
    ApprovalLog,
    Approved,
    Brief,
    Delivery,
    Ports,
    RecordEnd,
    Reply,
    ReportEnd,
    RunRecord,
    RunReport,
    Status,
    SystemAdapter,
    check_questions,
    sha256_of,
)
from ._events import Event, EventKind, EventLog
from ._facts import (
    ChallengeClass,
    ChecklistItem,
    Fact,
    FactSheet,
    appears,
    canary_in,
    load_fact_sheet,
    values_in,
)
from ._graded import Graded, GradedRun, MissingVerdicts, grade_run
from ._grading import SUCCESSES, FactResult, Grade, Outcome, QuestionMeasures, grade
from ._harness import Case, RunPorts, WorkspaceError, WorkspaceFor, read_record, run_case
from ._port_tools import port_server, serve_ports
from ._scripted import KINDS as SCRIPTED_KINDS
from ._scripted import Kind as ScriptedKind
from ._scripted import ScriptedSystem, scripted_system
from ._simulator import PICKER_PROMPT, Pick, Stakeholder, picker_agent, scripted_picker

__all__ = (
    "MAX_QUESTIONS_PER_CALL",
    "PICKER_PROMPT",
    "SCRIPTED_KINDS",
    "SUCCESSES",
    "ApprovalLog",
    "Approved",
    "Brief",
    "Case",
    "ChallengeClass",
    "ChecklistItem",
    "ChecklistJudge",
    "Coverage",
    "Delivery",
    "Event",
    "EventKind",
    "EventLog",
    "Fact",
    "FactResult",
    "FactSheet",
    "Grade",
    "Graded",
    "GradedRun",
    "ItemVerdict",
    "MissingVerdicts",
    "Outcome",
    "Pick",
    "Ports",
    "QuestionMeasures",
    "RecordEnd",
    "Reply",
    "ReportEnd",
    "RunPorts",
    "RunRecord",
    "RunReport",
    "ScriptedKind",
    "ScriptedSystem",
    "Stakeholder",
    "Status",
    "SystemAdapter",
    "WorkspaceError",
    "WorkspaceFor",
    "appears",
    "canary_in",
    "check_questions",
    "coverage",
    "grade",
    "grade_run",
    "load_fact_sheet",
    "picker_agent",
    "port_server",
    "read_record",
    "run_case",
    "scripted_judge",
    "scripted_picker",
    "scripted_system",
    "serve_ports",
    "sha256_of",
    "values_in",
)
