---
title: Build
description: Coming soon. The station that implements an approved design in an isolated workspace.
---

# Build

**Coming soon.** Build is the second station of the [software factory](software-factory.md). It will take an approved [design](design-station.md), and implement it: a coding agent works in an isolated workspace and hands on the code it built, as a diff of that workspace, with its test results. Nothing on this page runs yet.

## What it will do

- Take the approved `Design` as its input. Each acceptance criterion, such as "ticked tasks stay ticked after a reload", becomes something the build must show.
- Run a coding agent with Pydantic AI's workspace and tools, in a copy of the repository it cannot escape.
- Ask a person before any action that needs sign-off, as a [tool-call wait](approvals.md#tool-calls).
- Hand on the change itself: a diff of the workspace against where it started, with the results of the tests it ran. That diff, not a description of it, is what [Quality](quality-station.md) checks and Release delivers.

The [roadmap](roadmap.md) says where it sits in the order of work.
