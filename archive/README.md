# Archive

## Decision (2026-07-29)

`archive/pre-t0/` held pre-skeleton bytes (services, deploy presets, old
contracts). It was **untracked and gitignored** — one-disk only, invisible to
git backups. That state is forbidden.

**Resolution:** preserve as git history on orphan branch `archive/pre-t0`, then
delete from the working tree on `main`. Do not reintroduce an untracked
`archive/pre-t0/` directory.

- Branch: `archive/pre-t0` (orphan commit of the former tree)
- Working tree on `main`: removed
- `.gitignore`: no longer ignores `archive/pre-t0/`

Push the orphan branch when a remote exists (`git push -u origin archive/pre-t0`).
Nothing under the archived tree is part of the active import graph. Prefer
re-implementing against HTTP `cortex_client` and the five ports.
