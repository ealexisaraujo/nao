# Fork-Specific Instructions (ealexisaraujo/nao)

These instructions are local to this fork and are NOT committed to the upstream `getnao/nao` repository.

## .context/ Directory

The `.context/` directory contains **session context files** that document implementation decisions, architecture, and PR motivation. These files are fork-only (excluded via `.git/info/exclude`) and serve as persistent memory across coding sessions.

### Always read .context/ at the start of a task

Before starting any implementation work on this repo:

1. **List existing context files:** `ls .context/` to see what documentation exists
2. **Read relevant context:** If your task relates to an existing feature (dbt indexer, sync observability, etc.), read the matching `ctx_*.md` file first
3. **Check for the current branch:** Context files reference branches and PRs — find the one that matches your current branch

### Creating .context/ files

Create a new context file when:

- A PR is created or a significant feature is implemented
- An architectural decision is made that future sessions should know about
- A complex debugging session reveals important knowledge

**Naming convention:** `ctx_YYYY-MM-DD_<short-description>.md`

**Template:**

```markdown
# Context: <Title>

**Date:** YYYY-MM-DD
**PR:** <link> (if applicable)
**Branch:** `<branch-name>`
**Status:** <In progress | Complete | Merged>

---

## What this does
<Brief description>

## Why
<Motivation — the problem that was solved>

## Files changed
<Table of files and their purpose>

## Key decisions
<Architectural choices and trade-offs>

## How to verify
<Commands to test the implementation>
```

### Keeping .context/ files formatted

The `.context/` directory is NOT in `.prettierignore` upstream. To avoid pre-commit hook failures, always format context files after creating or editing them:

```bash
npx prettier --write ".context/*.md"
```

## Fork Workflow

This fork (`ealexisaraujo/nao`) contributes to upstream (`getnao/nao`) via pull requests:

- **Remote `origin`:** `ealexisaraujo/nao` (push here)
- **Remote `upstream`:** `getnao/nao` (PRs target here, push disabled)
- **Branch pattern:** `feat/<description>` or `fix/<description>`
- **PR target:** Always `getnao/nao:main`

### Before creating a PR

1. Ensure all hooks pass: `npm run lint`, `make lint` (cli/)
2. Format any `.context/` files: `npx prettier --write ".context/*.md"`
3. Do NOT commit `.context/` files to the PR — they are fork-only

### Build & deploy for testing

The Node.js backend compiles into a standalone binary. After TypeScript changes:

```bash
cd cli
python build.py --force     # rebuilds frontend + backend binary + copies FastAPI
pip install -e .            # reinstalls CLI package
```

Without `--force`, the binary is NOT rebuilt even if source changed. See `.context/ctx_2026-02-15_dbt-indexer-backend.md` for the full build pipeline.

## CONTRIBUTING.md Reference

The upstream project follows these conventions (see `CONTRIBUTING.md`):

- `npm run dev` — start frontend + backend in dev mode
- `npm run lint:fix` — fix lint issues before committing
- `npm run format` — format with Prettier
- Fork → feature branch → PR workflow
- Pre-commit hooks run: lint, format check, migration check, CLI lint
