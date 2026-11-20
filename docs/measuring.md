---
title: Measuring
description: Software Factory Evals, a benchmark of the design session, run on the factory and on a stock coding tool through the same ports and graded by plain code.
---

# Measuring

A factory, or a methodology inside one, is a claim: work done this way comes out better. Software Factory Evals tests that claim on the first thing any software work does, the design. It gives a system a vague request and a repository, lets it question a simulated stakeholder who knows what the request leaves out, and grades what it delivers with plain code. It runs the factory and a stock coding tool, Claude Code, through the same three ports, so their results can be compared.

The benchmark lives in the repository's `evals/` folder and is built on `pydantic-evals`, the evaluation library that comes with [Pydantic AI](https://pydantic.dev/docs/ai/). It is not part of either installed package. This page says what it measures, how far its numbers go, how to run it, and how a run is graded.

## What it measures

What is measured is the design session, mainly the questions it asks, not the format of what it writes. Every request leaves facts out on purpose. Some can be found by reading the repository; the rest only by asking. A good session asks about the facts that matter, leaves alone the ones that do not, reads what the code already says, and carries every answer into the design.

A case is a request, a repository and a fact sheet. The system sees the request and the repository. The fact sheet stays on the grader side: it holds the hidden facts, a canary and, for the scored cases, a checklist. Every fact a design must get right carries a value nobody could guess, such as "47 days", so plain code can tell whether a design states it.

Each case belongs to a class:

| Class | What the system must do |
|---|---|
| `critical-ambiguity` | Ask the stakeholder for each critical fact, and put the answers in the design |
| `in-the-repo` | Find a critical fact by reading the code, without asking |
| `complete` | Notice that nothing is missing, and deliver without needless questions |
| `nobody-knows` | Ask, learn that nobody knows a critical fact, and deliver the design as blocked |

Each case also names its rung: the repository its snapshot starts from. `empty` is an empty repository, so the design starts a new app. `r0` and `r1` are reference versions of an app, so the design adds a feature to code that already exists. The case may lay a few files of its own on top.

Today there is one case, the smoke case `todo` in the `smoke` split: a to-do page whose request says ticked tasks should not be kept for ever, on the `empty` rung. It exists to check the harness and is never scored. The `dev` and `test` splits, the reference apps behind `r0` and `r1`, and the checklists arrive in later pull requests.

## Its limits

- **Offline numbers prove the harness, nothing else.** Offline, every system and every model is scripted to behave a known way, so the result is true by construction. The command says so after every offline run.
- **No result has been published yet.** The command prints a scorecard and a run card after every experiment, but the only case today is the smoke case, which is never scored.
- **Small samples give wide intervals.** With about twelve cases, an interval on a rate is roughly 30 points either way, so only a large difference between arms can show. Every scorecard says so.
- **One stakeholder.** Each case has one simulated person in a one-to-one conversation, as in a Claude Code session. Several roles come later.
- **One model family.** Both the factory and Claude Code run on Claude Opus 5.5, and the checklist judge is Claude Sonnet 5.5. A result on other models is left for later.
- **The design, not the software.** No code is built from the design. Whether another design direction would have built better software waits for a later phase, where a builder works from the design.

## The three ports and the repository

A system receives a brief and works through two ports. Nothing else crosses.

| Port | What crosses it |
|---|---|
| The brief | The request, unchanged, and the case's repository as the working folder, read-only |
| The stakeholder | `ask_stakeholder(questions)`: one to four questions in a call, and one reply for each |
| Delivery | `deliver(document, status)`: the design as text, `delivered` or `blocked` |

There is no output contract, no template and no approval port, and a system is not told its spending cap. Each arm writes its design as it would.

**The repository.** A system that reads files sees one folder, the case's snapshot, mounted read-only at `/workspace` in a Docker container of its own. The snapshot is the rung's base with the case's own files on top, source only. Nothing else of the machine is in the container: not the fact sheets, not the simulator, not the graders. Before a run starts, plain code scans the snapshot for every hidden value and canary of every case, so a case that leaks its answers ends as an infrastructure error before the system sees it.

**The stakeholder.** No model writes a word a system sees. For each question, a small pinned model, Claude Haiku 5.5, picks only which facts the question asks about. Plain code then sends those facts' statements, word for word, as the reply. A question that asks about nothing on the sheet gets the sheet's fallback reply. A question that asks for sign-off gets the sheet's approval reply, and releases nothing. The picker reads one question at a time, and the same question asked again in a run gets the same reply.

Every question, reply and delivery is written to an append-only event log. The graders read that log and the delivered document. They never read what a system says about itself, beyond its cost and how it says it stopped.

## Run it

The smoke case runs offline, on scripted systems, with no Docker and no model.

```bash
uv run python -m evals --offline --split smoke
```

It prints one line for each run, then the scorecard and the run card, which [the scorecard and the run card](#the-scorecard-and-the-run-card) explains. Here is the start of what it prints, with Docker not running:

```text
scripted-asks-everything  todo  repeat 1  verified_design  turns 2  needless 1  checklist -
scripted-correct  todo  repeat 1  verified_design  turns 1  needless 0  checklist -
scripted-leaks-canary  todo  repeat 1  policy_failure  turns 1  needless 0  checklist -
scripted-silent  todo  repeat 1  design_failure  turns 0  needless 0  checklist -

Scorecard: Software Factory Evals 0.1

Split: smoke

Arm                       Runs  Verified design rate  Gate line  Reliability  Cost per verified
scripted-asks-everything  1     1.00                  0          -            $0.00
scripted-correct          1     1.00                  0          -            $0.00
scripted-leaks-canary     1     0.00                  1          -            -
scripted-silent           1     0.00                  0          -            -

How the session asked

Arm                       Obtained  Precision  Used  Ignored  Silent  Code asked  Needless
scripted-asks-everything  1.00      0.50       1.00  0.00     0.00    -           1.00
scripted-correct          1.00      1.00       1.00  0.00     0.00    -           0.00
scripted-leaks-canary     1.00      1.00       1.00  0.00     0.00    -           0.00
scripted-silent           0.00      -          -     -        1.00    -           0.00
```

The scorecard goes on with the rest of the question measures, checklist coverage and its notes, then the run card. The command ends with two notes:

```text
Offline: scripted systems and a scripted designer on a scripted picker. These numbers show the harness works. They say nothing about real models.
Docker is not running, so the factory, factory-simple arm(s) were left out. Start Docker to run them.
```

Each run line gives the arm, the case, the repeat, the outcome, the questions asked, the needless questions, and the checklist coverage, shown as `-` because the smoke case has no checklist. The four scripted systems are test fixtures. Each must get the outcome it is written to get, which is what "the harness catches it" means: the one that asks nothing fails, and the one that copies the canary is a policy failure. In the scorecard that shows as a gate line of 1.

With Docker running, build the sandbox image once. The same offline command then also runs the two factory arms on the scripted designer, each in its own container, and prints a line for each.

```bash
uv run python -m evals --build-image
uv run python -m evals --offline --split smoke
```

The same run from Python, with two of the scripted systems:

```python
import json
from pathlib import Path

from evals.__main__ import main

code = main(["--offline", "--arm", "scripted-correct", "--arm", "scripted-silent", "--out", "evals-out"])
print(code)
#> 0
for run in json.loads(Path("evals-out/results.json").read_text()):
    print(run["arm"], run["outcome"], run["turns"])
    #> scripted-correct verified_design 1
    #> scripted-silent design_failure 0
```

`main` returns 0 when every run was graded, whatever the outcomes, 1 when a run could not be graded, and 2 when the arms cannot run here or the experiment stopped.

**Options.**

| Option | What it does |
|---|---|
| `--offline` | Scripted systems, a scripted designer, a scripted picker and a scripted judge. Spends nothing |
| `--split smoke` | The split to run: `smoke`, `dev` or `test`. The default is `smoke` |
| `--arm NAME` | One arm to run; give it more than once for several. With none, every arm this mode can run here |
| `--repeat N` | Runs for each case in each arm. An experiment carries on where it stopped, so runs already in `--out` count |
| `--out FOLDER` | The run folder; `evals-out` by default |
| `--model NAME` | The model for the factory arms on real models; Claude Opus 5.5 by default |
| `--judge real` or `--judge scripted` | The checklist judge; scripted offline and real otherwise by default |
| `--regrade` | Grade every run in `--out` again from its stored record and verdicts, call no model, and print the scorecard and run card again. Add `--offline` when the verdicts came from the scripted judge |
| `--build-image` | Build the sandbox image, and run nothing |

Each run keeps its own folder, `<out>/<arm>/<case>/<repeat>/`, with `events.jsonl`, the whole `record.json` and, once judged, `checklist.json`. `<out>/results.json` holds one entry for each run: its outcome, whether it is a success, its questions, the counts behind every question measure, its checklist coverage, its cost and how often it was rerun. `<out>/scorecard.json` and `<out>/run-card.json` hold the scorecard and the run card of every run in the folder. An experiment stops after three runs in a row end as an infrastructure error; run the same command again to carry on.

**What needs what.**

| To run | You need |
|---|---|
| The scripted systems, offline | Nothing beyond `uv sync` |
| `factory` and `factory-simple`, offline | Docker and the sandbox image |
| `factory` and `factory-simple` on a real model | Docker, the image, and `ANTHROPIC_API_KEY`, which the picker and the judge use too |
| `claude-code` | Docker, the image, `ANTHROPIC_API_KEY` for the picker and the judge, and a Claude Code sign-in token in `CLAUDE_CODE_OAUTH_TOKEN`. It never runs offline |

**The author's live steps.** A real run spends money, so it is never run by CI or by these docs. Before a scored run, the author runs the live smoke test of Claude Code in its container, `uv run pytest -m live tests/test_evals_claude_code.py`, which [Contributing](contributing.md) describes, and then the smoke case on every arm with `uv run python -m evals --split smoke`. Its numbers are not published: the smoke case is never scored.

## The fact table and the outcomes

Plain code grades each run in a fixed order, cheapest and most decisive first. First it places every fact on the sheet in one row of the fact table.

| The fact is | Its value was released | The document and its status | Result |
|---|---|---|---|
| Critical, from the stakeholder | Yes | Value stated | Asked and used |
| Critical, from the stakeholder | Yes | Value not stated | Asked and ignored: a critical omission |
| Critical, from the stakeholder | No | Value stated | Unexplained value: a critical omission, read by hand |
| Critical, from the stakeholder | No | Not stated, delivered | Silent assumption: a critical omission |
| Critical, from the stakeholder | No | Not stated, blocked | Blocked when the answer was available |
| Critical, nobody knows | - | Blocked, after at least one question | Correct block |
| Critical, nobody knows | - | Blocked, with no question | Blocked without asking |
| Critical, nobody knows | - | Delivered | Silent assumption: a critical omission |
| Critical, in the repository | No | Value stated | Found in the code |
| Critical, in the repository | Yes | Value stated | Found by asking: a needless question |
| Critical, in the repository | Either | Value not stated | Missed in the code: a critical omission |
| Low | Yes | Either | A needless question |
| Low | No | Either | Correctly left alone |

An unexplained value may be a lucky guess, a matching error or a leak, and plain code cannot tell which, so it is graded as if absent and listed for a person to read. Only a canary is certain evidence of a leak.

Then each run gets one outcome. Where more than one applies, the first in this table wins.

| Outcome | When | Counted as |
|---|---|---|
| `infrastructure_error` | The harness, the program or a model provider failed, twice. A failed run is rerun once, the same for every arm | Failure, reported apart |
| `policy_failure` | The canary is in the delivered document or in any question | Failure |
| `budget_exhausted` | The run reached its spending cap or its time limit | Failure, reported apart |
| `design_failure` | No delivery, a critical omission, or a block on a case where nothing is unknowable | Failure |
| `correct_block` | On a nobody-knows case, the system asked at least one question and delivered as blocked | Success, reported apart |
| `verified_design` | Delivered, with every obtainable critical value stated. A stakeholder value must also have been released; a repository value need not | Success |

No model decides pass or fail; a calibrated judge reads each case's checklist, beside the result.

## The question measures

The question measures are named results reported beside the outcome. Each is computed by plain code from the event log, the fact sheet and the delivered document. A question is one `question_asked` event; a call with four questions is four.

| Measure | What it is | Better |
|---|---|---|
| Critical facts obtained by asking | Critical stakeholder facts released, out of those that can be obtained | Higher |
| Question precision | Questions that released a critical fact, out of all questions | Higher |
| Asked and used | Released critical stakeholder facts whose value is in the design | Higher |
| Asked and ignored | Released critical stakeholder facts whose value is not in the design | Lower |
| Silent assumptions | Critical facts a delivered design neither had released nor stated, counting a fact nobody knows | Lower |
| Asked what the code says | Repository facts released by a reply | Lower |
| Needless questions | Low facts released by a reply | Lower |
| Questions that released nothing | Questions whose reply released no fact | Lower |
| Questions per case | Questions asked in a run | Lower |

Each run's entry in `results.json` holds the counts behind these, under `questions`. The scorecard turns them into rates, per case and then across cases, with the case as the unit.

## The checklist and its judge

Stating every critical fact decides the outcome. It does not say whether the design took the right direction. For that, each scored case carries a hidden checklist of three to six items written by the author, such as "Lets a person undo a tick". A design may state an item anywhere, in any words.

A pinned judge model, Claude Sonnet 5.5, reads one item and the delivered document, and answers present or absent, with a quote when present. Plain code keeps a present answer only when its quote is an exact run of the document's text. The judge reads one item in each call, so one item cannot colour another, and it never sees which arm wrote the document, the conversation, the fact sheet or the other items. A design's coverage is the share of its case's items found present. Its verdicts are stored beside the run, so `--regrade` gives the same coverage without calling the judge.

Coverage sits beside the outcome and never changes it. Before any scored run, the author labels about 60 pairs of item and design from a pilot; below 90 per cent agreement with the judge, no coverage is published. The scorecard withholds coverage from the real judge until that agreement is known and met. The scripted judge used offline is right by construction, so its coverage is shown.

## The scorecard and the run card

After every experiment, and after `--regrade`, the command prints a scorecard and a run card for every run in `--out`, and writes them there as `scorecard.json` and `run-card.json`.

**The scorecard** has one row for each arm. Each split has a table of its own, so the dev and test cases are reported apart. There is no combined score. Its parts, in order:

| Part | What it holds |
|---|---|
| The headline | The verified design rate; the gate line, the count of policy failures, which must be zero before any other number is quoted; reliability, the chance that three attempts at a case all succeed; and the cost of each verified design |
| How the session asked | The question measures above, as rates; the questions and the minutes of a person's time per case, at two minutes a question; correct blocks on nobody-knows cases, beside the share that delivered instead; and the questions asked on complete cases |
| Design quality: beside the outcome, never deciding it | Checklist coverage over the runs that delivered, with the runs that delivered nothing counted beside it |

Below the tables come the paired difference in verified design rate between `factory` and `claude-code`, the primary comparison, and between `factory` and `factory-simple`, the ablation, labelled exploratory; each appears only when both arms ran. Then every unexplained value with its run id, for reading by hand; how often each checklist item was covered; the runs that were rerun; and the limits every scorecard states.

A metric is left as `-` when no case gives it a denominator, such as the repository measure on a split with no in-the-repo case. Reliability needs three repeats, and cost per verified design needs every run's cost.

**The statistics.** The case is the unit of analysis, because repeats of one case are not independent. A rate is each case's own rate, averaged across cases, with a 95 per cent interval of the mean plus and minus t times the standard error between cases. A comparison is the difference case by case, with an interval from the spread of those differences. A rate with an interval prints as its mean and then its bounds in brackets, such as `0.50 [0.09, 0.91]`. With one repeat, or one case, there is no interval and the scorecard says so. The t values for 1 to 30 degrees of freedom are kept in a small table in `evals/stats.py`, with no statistics library; it also holds a Wilson interval, for a plain count of independent runs only, and `pass_k`, for reliability.

```python
from evals.stats import clustered_rate, pass_k, wilson

low, high = wilson(18, 30)
print(round(low, 2), round(high, 2))
#> 0.42 0.75
print(pass_k(4, 5, 1), pass_k(4, 5, 3))
#> 0.8 0.4
mean, low, high = clustered_rate([0.2, 0.4, 0.6, 0.8])
print(round(mean, 2), round(low, 2), round(high, 2))
#> 0.5 0.09 0.91
```

**The run card** says what the results were measured with: the benchmark's version, its cap and time limit, every model, the hashes of the picker's prompt, the judge's prompt and the port tools' descriptions, the hash of every case and of its checklist, the hash of each reference app, the sandbox image's digest, the Pydantic AI Harness version, the splits, repeats and arms, the factory's commit, the hash of the factory's skill library, the designer's cap on rounds of questions, Claude Code's version and options and the plan of its sign-in, the judge's agreement with the author's labels, the simulator's pass rates and error rate, and the date. The skill library's hash is SHA-256 over every `SKILL.md` of Elephant Goldfish and then of the software package, each in path order. An item whose source is missing, such as Docker on a machine without it or a labelled sample not yet taken, says "not available".

## The arms

An arm is one system configuration run on every case. Every arm gets the same request and the same repository, and nothing else.

| Arm | System | Runs |
|---|---|---|
| `claude-code` | Vanilla Claude Code without a terminal, with its read tools `Read`, `Grep` and `Glob` and the two port tools, served to it as an MCP server. It loads the repository's project settings only, never a user's own | On the pinned model only, in a container |
| `factory` | The shipped `SoftwareFactory(model, workspace=...)`, unchanged: the [Design station](design-station.md) with [Elephant Goldfish](elephant-goldfish.md)'s independent review | Offline on the scripted designer, or on a real model, in a container |
| `factory-simple` | The simple designer: the same factory with `design=design_station(model, method=ElephantGoldfish(goldfish=()), workspace=...)`. The same designer, instructions, skills, design shape, questions in rounds and gate, with only the review left out | As `factory` |

The primary comparison is `factory` against `claude-code`. `factory` against `factory-simple` is the ablation: it measures what independent review adds, with everything else held equal.

The factory's own approval waits are answered by the adapter, as a person who accepts what the station shows them would: an approval is given at once, and a round of questions from `ask_person` goes to the stakeholder port as one call. When rework runs out, the last draft is delivered as blocked.

The four `scripted-` arms are the test fixtures shown above. They are built from the fact sheet, run only offline and are never reported.

To plug in another system, write an adapter with a `name` and an `async run(brief, ports)` that asks through `ports.ask_stakeholder` and ends with `ports.deliver`. `evals/contract.py` holds the contract. It imports nothing from the factory, and only the factory adapter may.
