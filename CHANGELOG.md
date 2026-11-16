# Changelog

Every change a user can see is listed here, newest first.

## Unreleased

- Set up the repository: a uv workspace with the generic core (`pydantic-ai-factory`) and the software domain package (`pydantic-ai-factory-software`), formatting, linting, strict typing, tests, an import-rule check and CI on Python 3.11 to 3.14.
- Add the documentation site, with Overview, Concepts and Decisions pages, diagrams, a check that every diagram parses, and a test that runs every Python sample in the docs.
- Add a keep-out scan that stops credentials, personal paths and private terms from being published.
