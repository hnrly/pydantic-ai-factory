"""The systems the harness can run, each behind the port contract.

The scripted systems are grader-side test fixtures. The factory adapter runs the Design
station at full strength as the arms ``factory`` and ``factory-simple``, and is the only
module here that imports the factory. The Claude Code adapter runs Claude Code as a
program, in a container, as the arm ``claude-code``; it reaches the ports through the
port tools.
"""
