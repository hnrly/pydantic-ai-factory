---
title: Quality
description: Coming soon. The station that checks a build with tests and review, and sends it back when it fails.
---

# Quality

**Coming soon.** Quality is the third station of the [software factory](software-factory.md). It will check what [Build](build-station.md) produced against the approved design, and send the work back when it falls short. Nothing on this page runs yet.

## What it will do

- Run the build's tests and record the results as evidence. A test run that could not start is "did not run", never a pass.
- Have the change reviewed by fresh goldfish under the [Elephant Goldfish](elephant-goldfish.md) methodology, against the design's acceptance criteria.
- Fail the build through a plain-code [gate](gates.md) when a test fails or a blocking finding is open. A [rework rule](factories.md#rework) sends it back to Build. If a ticked task comes back after a reload, this is where it is caught.
- Escalate to a person when rework runs out.

The [roadmap](roadmap.md) says where it sits in the order of work.
