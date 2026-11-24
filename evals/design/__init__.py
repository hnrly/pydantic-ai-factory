"""Software Factory Evals: a benchmark of how well a system designs software.

It is a suite of :mod:`pydantic_ai_factory.evals`. It runs a system on cases whose
requests leave facts unsaid, through one port contract, with a simulated stakeholder who
holds the facts. Plain code grades each run. The one command is
``python -m evals.design``; ``python -m evals.design --offline --split smoke`` checks the
harness without spending money.
"""
