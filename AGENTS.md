# Agents.md

## Development best practices

- ALWAYS confirm the underlying assumptions and the intended architectural approach with the user before proceeding
- Simplicity First: minimal edits, no speculative flexibility / abstractions
- Surgical changes: Do NOT change code, comments, and formatting if not required

## Preferred software

- ALWAYS ask the user if existing software, framework, and libraries can be used - DO NOT blindly build everything from scratch.
- ruff for linting and formatting.
- mypy for static type checking.
- pydantic for type validations
- pydantic-settings to manage configuration variables and settings

## Data Science

- Use Polars for data tables.
- Use Marimo instead of Jupyter notebooks.
- Use scikit-learn for classical machine learning tasks.

## Documentation

- Add a docstring to each function
- Whenever a task is completed, review and update existing documentation

## Quality checks

At the very end of a task run these checks:

```bash
uv run ruff check .
uv run ruff format .
uv run mypy
uv run pytest
```

- Fix all errors until all checks pass.
- Verify that no credentials are tracked via git
- Remove any dead code created by your changes.