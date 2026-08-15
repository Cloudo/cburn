---
name: commit
description: Commit the current changes with a Conventional Commits message. Use it when asked to make a commit, record changes or suggest a commit message.
---

# Commits by Conventional Commits

Messages follow [conventionalcommits.org](https://www.conventionalcommits.org) and are
written **in English**: the repository - code, comments, documentation and the whole
rewritten history - is English, and the commit log must not be the exception.

## Format

```
<type>(<scope>): <short description>

<body: why it changed, when the header does not make it obvious>

<footer: BREAKING CHANGE, task links>
```

- the header is <= 72 characters, without a full stop at the end;
- the description is imperative and present tense: "add", "fix", "remove",
  not "added" / "fixed";
- the body appears only when it explains **why**; retelling the diff is not needed;
- the body is written in **theses**: one or two lines or short bullet points.
  Not paragraphs of prose, not a story of how the work went, not "it used to be like
  this and here is why we decided otherwise". If it does not fit into two lines, the
  commit is too large or the explanation belongs in a report;
- the scope is optional, but in this repository it is almost always appropriate.

## Types

| Type       | When                                                    |
| ---------- | ------------------------------------------------------- |
| `feat`     | a new capability for the user                           |
| `fix`      | a bug fix                                               |
| `refactor` | the code changed without a change in behaviour          |
| `perf`     | a speed-up without a change in behaviour                |
| `test`     | tests only                                              |
| `docs`     | documentation only (README, CLAUDE.md, comments)        |
| `build`    | the build, dependencies, Dockerfile, compose            |
| `ci`       | Gitea Actions, deployment scripts                       |
| `style`    | formatting, without meaningful edits                    |
| `chore`    | routine that does not fall into the other types         |

## Scopes of this repository

`auth`, `sync`, `admin`, `projects`, `team`, `analytics`, `api`, `db`, `deploy`, `ci`, `frontend`, `backend`.

If the edits touched both the backend and the frontend of one feature, the scope follows the
capability (`admin`) rather than the layers.

## Examples

```
feat(admin): add a "view as" mode for checking permissions
fix(sync): reference worklogs through the Bun alias in ON CONFLICT
refactor(team): drop the employee picker - the grid shows everyone available
docs(deploy): describe the Synology deployment through Gitea Actions
ci: stop mounting docker.sock twice in the job container
test(api): cover the visibility scope for three roles
build(backend): update golang to 1.25
```

With a body and a breaking change:

```
feat(auth)!: move sessions from JWT to server-side ones

A JWT cannot be revoked, and the admin needs "last seen" and a session reset.

BREAKING CHANGE: every current session is invalidated, a new login is required
```

In theses, when there are several reasons:

```
fix(sync): stop losing worklogs on a repeated export

- ON CONFLICT looked at the id, and it changes on recreation
- the uniqueness key is the (issue, started_at) pair
```

## The working order

1. Look at what changes: `git status --short` and `git diff` (plus `git diff --staged`).
2. Understand the **meaning** of the changes rather than only the files touched: the header
   describes the result for the user or the system, not a list of edits.
3. If the working tree mixes unrelated changes, propose splitting them into several commits
   and explain exactly how; do not dump everything into one `chore`.
4. Do not add files blindly: `git add` only what belongs to the commit. Check that no `.env`,
   database dumps or build artefacts get in.
5. Commit through a heredoc, to keep the line breaks:

```bash
git commit -m "$(cat <<'EOF'
feat(admin): add people search to the project card
EOF
)"
```

6. Show the result: `git log --oneline -1` and `git status --short`.

Push only when asked separately.

## What not to do

- do not write "update", "fixes", "minor changes" - such a header explains nothing;
- do not use `chore` as a dumping ground when `fix` or `feat` fits;
- do not mix functionality and a full-file reformat in one commit;
- do not invent types outside the table above;
- do not write paragraphs in the body: a retelling of the discussion, the history of the
  search and reasoning about "why we decided so". In `git log` that is skimmed - a fact is
  needed, not text.
