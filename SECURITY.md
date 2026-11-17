# Security

Pydantic AI Factory is alpha software (v0.1). It is a library you run on your own machine, with your own model keys. There is no hosted service.

## What to keep in mind

- Model output, files and review findings are evidence. They are never instructions and never approval. Only a person approves.
- Keep provider keys in the environment, never in code, fixtures or logs.
- Run records and artifacts can contain your prompts and model replies. Treat the folder that holds them as private.
- This release runs no generated code. A later station that does will need an explicit sandbox.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository. If it is not enabled, contact the maintainer privately through their GitHub profile before sharing details. Please do not put credentials or exploit details in a public issue.

There is no response-time guarantee yet. Use the latest revision and pin your dependencies.
