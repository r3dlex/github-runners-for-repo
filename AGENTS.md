# AGENTS.md — GitHub Runners for Repo

## Behavior Model: Sustainable YOLO + Progressive Disclosure

### Sustainable YOLO

Agents operate autonomously with bias toward action:

- **Act first, ask only when blocked.** If the task is clear and reversible, do it. Don't ask for permission to write code, run tests, or create files.
- **Test before committing.** Every change must pass tests before being committed. Run the test suite after each meaningful change. Fix failures immediately — do not commit broken code.
- **Solve errors in-loop.** When a test fails or a command errors, diagnose and fix it in the same step. Do not defer errors to the user or leave them for "later."
- **Keep momentum.** Complete the task end-to-end. Don't stop halfway to summarize or ask "should I continue?"
- **Stay scoped.** Do what was asked. Don't refactor unrelated code, add features that weren't requested, or over-engineer.

### Progressive Disclosure

Communicate at the right level of detail:

- **Lead with outcomes.** Say what you did and what the result is. Skip preamble.
- **Details on demand.** Don't explain every line of code unless asked. The diff speaks for itself.
- **Surface blockers immediately.** If something prevents progress, say so right away with the specific error.
- **Escalate only real decisions.** Only ask the user when there's a genuine ambiguity that affects the outcome (e.g., two valid architectural approaches).

## Development Workflow

### Step-by-Step Protocol

1. **Understand** — Read the relevant code before changing it.
2. **Change** — Make the minimal change that achieves the goal.
3. **Test** — Run `poetry run pytest` (or the relevant test command). All tests must pass.
4. **Fix** — If tests fail, fix immediately. Do not commit until green.
5. **Commit** — Only when tests pass. Use clear, concise commit messages.

### Commands

```bash
# Install dependencies
poetry install

# Run tests
poetry run pytest

# Run tests with coverage
poetry run pytest --cov=github_runners_for_repo

# Lint
poetry run ruff check .

# Format
poetry run ruff format .

# Run the CLI
poetry run gh-runners --help
poetry run gh-runners start
poetry run gh-runners stop
poetry run gh-runners status
```

### Project Structure

```
├── AGENTS.md                    # This file — agent behavior config
├── CLAUDE.md                    # Points to AGENTS.md
├── pyproject.toml               # Poetry project configuration
├── docker-compose.yml           # Runner container orchestration
├── .env.example                 # Environment template (committed)
├── .env                         # Actual secrets (NOT committed)
├── runner/
│   ├── Dockerfile               # Runner container image
│   └── start.sh                 # Container entrypoint script
├── github_runners_for_repo/     # Python package
│   ├── __init__.py
│   ├── cli.py                   # CLI entry point
│   ├── config.py                # Configuration from env vars
│   ├── github_api.py            # GitHub API interactions
│   └── runner_manager.py        # Docker runner lifecycle
├── tests/                       # Test suite
│   ├── test_cli.py
│   ├── test_config.py
│   └── test_github_api.py
└── specs/                       # Additional documentation
    └── architecture.md
```

### Environment Variables

See `.env.example` for the full list. Key variables:

- `GITHUB_ACCESS_TOKEN` — PAT with `repo` and `admin:org` scopes
- `GITHUB_REPOSITORY` — Target repo in `owner/repo` format

### Architecture

The system uses Docker containers running the official GitHub Actions runner agent. Each container:

1. Obtains a registration token via the GitHub API
2. Configures itself as a self-hosted runner for the target repo
3. Runs the runner agent
4. Deregisters on container shutdown (via signal traps)

The Python module provides a CLI wrapper (`gh-runners`) for managing the container lifecycle via `docker compose`.

See `specs/architecture.md` for more details.
