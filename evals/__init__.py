"""Software Factory Evals: a benchmark of how well a system designs software.

It runs a system on cases whose requests leave facts unsaid, through one port contract,
with a simulated stakeholder who holds the facts. Plain code grades each run. The one
command is ``python -m evals``; ``python -m evals --offline --split smoke`` checks the
harness without spending money.
"""
