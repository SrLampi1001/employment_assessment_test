# Agents Guidelines

This document outlines norms for AI/LLM agents (or human contributors) working on the **Riwi Co. Messaging Platform** project.

---

## Branching & Version Control

### Branching Model
```mermaid
gitGraph
    commit
    branch develop
    checkout develop
    branch feature/AGENTS-123-add-rls
    commit
    commit
    checkout develop
    merge feature/AGENTS-123-add-rls
    branch release/v1.0.0
    commit
    checkout main
    merge release/v1.0.0
```

### Rules
- **Never work on `main`**:
  - `main` is **immutable** (only for releases).
  - Use `develop` as the base for new branches.
- **Branch Naming**:
  - **Feat**: `feat/description` (e.g., `feat/AGENTS-123-add-rls`).
  - **Fix**: `fix/-description` (e.g., `fix/AGENTS-456-fix-jwt`).
  - **Release**: `release/vX.Y.Z` (from `develop`).
  - **Hotfix**: `hotfix/[JIRA-TICKET]-description` (from `main`).
- **Pull Requests (PRs)**:
  - Target `develop` (unless hotfix → `main`).
  - Require **Human approval** before merge and **passing CI checks**.
  - All branches, expect release branches, are merged via squash merge and deleted.
  - Release branches target main.
  - **PR workflow mode** (this project is **feature-oriented**, not single-commit):
    - **Branch**: create a feature branch from `develop`.
    - **Commits per feature**: multiple commits inside the branch — one per logical unit (`feat:` for code, `test:` for tests, `fix:` for bugfix, `chore:` for maintenance). Don't squash commits *into* the branch; squash happens at merge time.
    - **Push**: push the branch to `origin`.
    - **PR**: open the PR against `develop`, fill the template (or rationale + summary if no template), request review.
    - **Merge**: squash-merge once approved + green CI. The branch is auto-deleted; the squash commit preserves the full commit history on `develop`'s log.
  - **What this implies for AI agents**: don't try to cram code + test + bugfix into one commit, and don't open a PR with a single "wip" commit. Make small, named commits inside the feature branch and let reviewers follow the work.

---

## Commit Messages
Use **[Conventional Commits](https://www.conventionalcommits.org/)**.
**Format**: `<type>(<scope>): <description>`

| Type       | Usage                          | Example                                  |
|------------|--------------------------------|------------------------------------------|
| `feat`     | New feature                    | `feat(auth): add JWT refresh rotation`   |
| `fix`      | Bug fix                        | `fix(db): correct RLS policy for guests`|
| `docs`     | Documentation                  | `docs: add AGENTS.md norms`               |
| `refactor` | Code refactor (no new features)| `refactor(api): apply SOLID to services`  |
| `test`     | Tests                          | `test(db): add RLS permission test`      |
| `chore`    | Maintenance (e.g., dependencies)| `chore: update PostgreSQL to v15`        |

#### Avoid:
- Vague messages (`fix: stuff`).
- Long descriptions (use PR body for details).

---

## Documentation Norms

### Diagrams > Text/Images
- **Always use Mermaid** for:
  - Architecture.
  - Database schemas.
  - Workflows.
- **Example: ER Diagram**
  ```mermaid
  erDiagram
      rw_user ||--o{ rw_message : sends
      rw_user ||--o{ rw_channel : member_of
      rw_message }|--|| rw_channel : belongs_to
      rw_user {
          uuid rw_id PK
          string rw_username
          string rw_password_hash
          timestamptz rw_created_at
      }
      rw_message {
          uuid rw_id PK
          uuid rw_user_id FK
          uuid rw_channel_id FK
          text rw_content
          boolean rw_is_edited
          timestamptz rw_created_at
      }
  ```

### Code Snippets
- **Short and focused** (≤10 lines).
- **Syntax-highlighted** (use Markdown ```lang).
- **Annotate** non-obvious logic:
  ```sql
  -- RLS Policy: Users can only see messages in their channels
  CREATE POLICY message_access_policy ON rw_message
      USING (rw_channel_id IN (
          SELECT rw_channel_id
          FROM rw_channel_member
          WHERE rw_user_id = current_setting('app.current_user_id')::uuid
      ));
  ```

### Cross-References
- Link to:
  - **External docs**: [PostgreSQL RLS](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).
  - **Internal files**: See [README.md](./README.md#security) for auth details.

---

## Prohibited Actions
| Action | Reason |
|--------|--------|
| Committing to `main`        | Breaks CI/CD pipeline.      |
| Hardcoding secrets          | Use `.env` + `gitignore`.   |
| SQL string concatenation    | Security risk (SQL injection). |
| Bypassing RLS (`BYPASSRLS`) | Violates core security rule.   |
| Physical message deletion   | Audit trail must be preserved.  |

---

## Python Development Environment

The Python toolchain must be **isolated and reproducible**. AI agents and human contributors (including grading reviewers in a fresh sandbox) should be able to clone the repo, run one command, and get a working environment with the exact pinned dependencies — no surprises, no system-level `pip install`.

- **Use a virtual environment.** All Python work happens inside a project-local `.venv/` (or `.venv3.13/`). System Python is never used directly.
- **`.venv/` is gitignored.** A leaked virtualenv is larger than the project itself and breaks reproducibility (it pins to an OS / Python build).
- **Pin Python.** The project targets **Python 3.13**. `.python-version` (or `pyproject.toml`'s `requires-python = "==3.13.*"`) records the floor so any developer — or CI runner — picks the right interpreter.
- **Dependency manager:** `uv` is preferred (fast, lockfile-driven, ships its own Python). `pip-tools` is acceptable as a fallback if `uv` is unavailable.
- **Lockfile is committed.** `uv.lock` (or `requirements.lock`) is the source of truth for transitive pins. The bare `pyproject.toml` / `requirements.txt` declares direct dependencies; the lockfile fixes their versions.
- **Reproducibility check:** the project must boot on a clean machine from `uv sync` (or `pip install -r requirements.lock`) alone — no manual steps, no `apt-get install`, no global `pip install`.

Forbidden:

- `pip install <package>` against system Python.
- Committing `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`.
- Hand-editing a lockfile (let the tool regenerate it).
- Adding a runtime dependency without updating `pyproject.toml` AND the lockfile in the same commit.

---

## Skill Maintenance

Skills under `.agents/skills/` are **AI-agent guardrails**, not the project's source of truth — `ARCHITECTURE.md` is. When real code is shipped that fulfills an example in a skill, the skill must be updated so it does not drift away from reality.

### What to keep vs. replace

| Code in a skill | Treatment |
|---|---|
| Descriptive patterns (`function send_message(...)`, generic idiomatic snippets, layer-diagram code) | **Keep.** These teach the AI the *shape* of correct code. They may go slightly stale on syntax details; that's acceptable. |
| Feature-specific code that mirrors shipped functionality (`@router.post("/channels/{id}/messages")` if `/backend/app/delivery/http/messages.py` already has the real handler) | **Replace with a reference** to the actual file and a one-line summary of what it does. The skill should not contain a second copy of the truth. |
| Version snapshots (table rows that pin versions) | **Keep**, but mark the date and verify against the project's `pyproject.toml` / `package.json` in PR review. If a pin drifts, open a follow-up. |

### When code ships

Every PR that introduces or moves a feature file **MUST** be followed (in the same PR or in an immediate `chore:` follow-up PR) by:

1. A scan of `.agents/skills/*/SKILL.md` and `references/*.md` for predictive code blocks that now duplicate the shipped file.
2. A replacement of those blocks with a `See /path/to/actual/file.py` reference plus a one-line summary of what the file does.
3. A bump of `PROMPT_VERSION` if the change is inside the AI provider's system prompt (so the audit row can bisect).

The follow-up commit uses a `chore(skills):` prefix and is **feature-branch-scoped**, not a global rewrite — only the skill files touched by the original feature get the reference replacement.

### Why this matters

The alternative is the skill becoming a "second source of truth" that contradicts the code. Once the AI has read both, it can't tell which one to follow — and it will pick the one that fits its training-data priors, which is usually the wrong one. The first time this drifts, it becomes a permanent habit.

---

## Checklist Before PR
- [ ] Branch follows naming convention.
- [ ] Commit messages are conventional.
- [ ] Mermaid diagrams replace static images/text.
- [ ] No secrets in code (use `.env`).
- [ ] Tests pass locally.
- [ ] Documentation updated (if applicable).

---
**🔗 See Also**:
- [README.md](./README.md) (Project Overview)
- [CONTRIBUTING.md](./CONTRIBUTING.md) (Human Contributor Guide)