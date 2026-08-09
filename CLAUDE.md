# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

This project is scaffolding only: `src/`, `tests/`, and `doc/` exist but are empty, and there is no
`pyproject.toml` yet. Expect to establish the project layout (uv-managed Python package under `src/`,
pytest suite under `tests/`, docs under `doc/`) as part of the first substantive task — confirm the
intended structure with the user before creating it.

## Commands

Tooling is run through `uv`. At the end of every task, run all four and fix failures until they pass:

```bash
uv run ruff check .
uv run ruff format .
uv run mypy
uv run pytest
```

Single test: `uv run pytest tests/test_foo.py::test_bar`

## Conventions

Its non-obvious requirements:

- Confirm underlying assumptions and the intended architectural approach with the user **before**
  implementing. Ask whether an existing library or framework can be used instead of building from scratch.
- Surgical changes only — do not touch code, comments, or formatting that the task does not require,  
  and do not add speculative abstractions.
- Every function gets a docstring; update existing docs when a task completes.

## Preferred libraries

pydantic (validation), pydantic-settings (configuration), Polars (data tables), Marimo (notebooks —
not Jupyter), scikit-learn (classical ML).
