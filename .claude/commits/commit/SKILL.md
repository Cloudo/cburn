---
name: commit
description: Commit the current changes with a Conventional Commits message written in Russian. Use it when asked to make a commit, record changes or suggest a commit message.
---

# Commits by Conventional Commits

Messages follow [conventionalcommits.org](https://www.conventionalcommits.org), but the
**description is written in Russian**. Only the type, the scope and the service words
(`BREAKING CHANGE`) stay English.

## Format

```
<type>(<scope>): <short description>

<body: why it changed, when the header does not make it obvious>

<footer: BREAKING CHANGE, task links>
```

- the header is <= 72 characters, without a full stop at the end;
- the description is imperative and present tense: "добавить", "починить", "убрать",
  not "добавил" / "добавлено";
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
feat(admin): добавить режим «Смотреть как» для проверки прав
fix(sync): ссылаться на worklogs через алиас Bun в ON CONFLICT
refactor(team): убрать выбор сотрудников — сетка показывает всех доступных
docs(deploy): описать развёртывание на Synology через Gitea Actions
ci: не монтировать docker.sock повторно в job-контейнере
test(api): покрыть скоуп видимости для трёх ролей
build(backend): обновить golang до 1.25
```

With a body and a breaking change:

```
feat(auth)!: перевести сессии с JWT на серверные

JWT нельзя отозвать, а админу нужны «когда заходил» и сброс сессий.

BREAKING CHANGE: все текущие сессии инвалидируются, потребуется повторный вход
```

In theses, when there are several reasons:

```
fix(sync): не терять worklogs при повторной выгрузке

- ON CONFLICT смотрел на id, а он меняется при пересоздании
- ключ уникальности — пара (issue, started_at)
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
feat(admin): добавить поиск людей в карточке проекта
EOF
)"
```

6. Show the result: `git log --oneline -1` and `git status --short`.

Push only when asked separately.

## What not to do

- do not write "обновление", "правки", "мелкие изменения" - such a header explains nothing;
- do not use `chore` as a dumping ground when `fix` or `feat` fits;
- do not mix functionality and a full-file reformat in one commit;
- do not invent types outside the table above;
- do not write paragraphs in the body: a retelling of the discussion, the history of the
  search and reasoning about "why we decided so". In `git log` that is skimmed - a fact is
  needed, not text.
