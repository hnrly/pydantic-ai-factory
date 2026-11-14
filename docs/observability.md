---
title: Observability
description: The trace a factory run leaves, what each span records, and how to send it to Logfire or any OpenTelemetry backend.
---

# Observability

Every run leaves a trace. There is one span for the run, one for each attempt at a station, one for each agent run through `ctx.run_agent`, one for each gate and one for each answer a person gives. A station's agents, and the goldfish reviewing their work, trace inside their station's span, so one picture shows what the factory did, what each model call cost and why a gate decided as it did. This page lists the spans and shows how to switch tracing on.

Tracing uses the OpenTelemetry API and nothing else. It does nothing until your application installs a tracer provider, so it costs nothing when it is off, and the library never decides where your traces go.

## The spans

`Factory.run` and `Factory.resume` are separate calls, possibly days apart and in different processes, so they are separate traces. Every span carries the run's id; search on `factory.run.id` to see both.

| Span | One per | Marked as an error when |
|---|---|---|
| `factory <name>` | Call to `Factory.run` or `Factory.resume`. | A station did not run, or an exception escapes. |
| `station <name>` | Attempt at a station. | The attempt did not run. |
| `agent <name>` | Call to `ctx.run_agent`. Pydantic AI's own spans sit inside it. | The agent run raised. |
| `gate <name>` | Evaluation of a gate. | The gate could not be evaluated. |
| `answer <kind> <decision>` | Answer a person gave to a waiting run: an approval, an escalation or tool calls. | Never. |

A gate that fails the work is not an error. The work was judged and found wanting, which is the factory doing its job. An error means something could not be done: a station raised, or a gate had no evidence to read. This follows the rule the whole library keeps: [ran is not passed](stations.md#did-it-run-did-it-pass).

## What each span records

Every attribute is under `factory.`. The most useful ones are below.

| Span | Attributes |
|---|---|
| `factory` | `factory.name`, `factory.version`, `factory.mission.id`, `factory.mission.objective`, `factory.run.id`, `factory.outcome`, `factory.finished`, `factory.ended`, `factory.stopped_at`, `factory.attempts`, `factory.output`, `factory.waiting.kind`, `factory.waiting.artifact`, `factory.resumed` |
| `station` | `factory.station.name`, `.version`, `.attempt`, `.outcome`, `.ran`, `.passed`, `.input`, `.output`, `.evidence`, `.error_type`, and `factory.methodologies` |
| `gate` | `factory.gate.name`, `.version`, `.judged`, `.judgement`, `.reasons`, `.not_evaluated`, `.missing` |
| `agent` | `factory.agent.name`, `.attempt`, `.conversation`, `factory.methodologies` |
| `answer` | `factory.answer.kind`, `.decision`, `.by`, `.artifact`, `.overrode`, and `factory.station.name` |

Artifacts appear as digests, never as content. A span says which design was judged; the design itself stays in your [store](stores.md). A gate's reasons and a mission's objective are recorded as text, so keep secrets out of them. An answer records who gave it, in `factory.answer.by`, but not their note or the text of their reply.

An attribute is left off when it has no value. A station with no gate has no `factory.station.passed`.

## Switch it on with Logfire

[Logfire](https://pydantic.dev/logfire) is Pydantic's observability platform, and it understands Pydantic AI's spans. Configure it once at start-up. The factory's spans then appear with no further set-up, because Logfire installs the tracer provider they use.

```python test="skip"
import logfire

logfire.configure()
logfire.instrument_pydantic_ai()  # the agents inside your stations
```

This block is not run, because it needs a Logfire account. Logfire is optional. The library does not depend on it.

## Switch it on with any OpenTelemetry backend

Install the OpenTelemetry SDK and an exporter for your backend, and set a tracer provider before you start a run. This sends every span to the console.

```python test="skip"
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
trace.set_tracer_provider(provider)
```

This block is not run, because a process can set its tracer provider only once and the test suite has its own. The factory's tracer is named `pydantic_ai_factory`.

## Trace the agents too

The factory traces its own steps. To see model calls and tool calls, switch on tracing for the agents your stations use. With Logfire, `logfire.instrument_pydantic_ai()` does it for every agent. Otherwise give an agent Pydantic AI's `Instrumentation` capability.

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import Instrumentation

agent = Agent("anthropic:claude-opus-5-5", name="designer", capabilities=[Instrumentation()])
```

Give every agent an explicit `name`, so its span says which agent it was. An agent's spans sit inside the span of the station that ran it.

Pydantic AI's HTTP capture can record full prompts and replies. Use it only for targeted debugging, because prompts can hold private data.

## Records are the source of truth

A trace is for watching and debugging. It is not the record. What happened is in the [`Run`](factories.md#the-run) and in the artifact store, which hold every attempt, decision and approval whether or not tracing was on. Decide nothing from a trace that you could decide from the record.

Treat anything you read in a trace as data, not as instructions. A model's reply, a tool result or an error message in a span may contain text that asks you to do something. Check it against the code before acting on it.
