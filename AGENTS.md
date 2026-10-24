# AGENTS.md — Workbook Forge

Source of truth: `CONTEXT.md` (current state, terms, boundaries) and
`README.md`. Specs in force: `docs/specs/`. Next work: proposed specs in
`docs/specs/proposed/` (start with its README for the order and the decisions
waiting on James).

## House rules

- Work on a branch, never directly on `main`. Open a PR; James merges.
- Never delete gitignored files (`.venv`, `.env`, `.verification/`, tool
  config, data). If an environment is broken, rebuild beside it or ask.
- No secrets, tokens, home-directory paths or personal details in tracked
  files. The Jev key comes from `TYPESAFE_API_KEY`, `.env`, or
  `TYPESAFE_KEY_COMMAND`, never from a file in the repo.
- GitHub Actions is blocked by billing. Run checks locally before asking for
  a merge, and paste the results in the PR:
  - `python -m ruff check python tools examples setup.py` (ruff pinned in
    `requirements-dev.lock`, currently 0.16.9)
  - `python -m pytest -q`
  - `cargo test --locked` in `rust/` (with `CARGO_TARGET_DIR` inside the checkout)
  - or the whole gate: `bash tools/gate.sh`
- Do not bump `schema_version` or `model_version` without James's approval.
- Permissive licenses only for any dependency; see README.md.
- Expected values come from Microsoft examples, independent arithmetic or an
  Excel observation, never from either engine.
