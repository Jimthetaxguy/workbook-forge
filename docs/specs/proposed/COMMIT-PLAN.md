# Commit plan for the grok-build session (workbook-forge)

James, the session has uncommitted edits sitting on `main`:

- `python/tests/test_canonical_model.py`
- `python/tests/test_excel_oracle.py`
- `tools/excel_oracle.py`
- `rust/src/model.rs` (sha256 chunking change)

Nothing has been committed, so it's easy to move this work to a branch now.
Ask the session to:

1. Create a branch. Uncommitted edits come along with it:
   `git switch -c grok/excel-oracle-<date>` (for example `grok/excel-oracle-2026-10-14`).
2. Commit in logical pieces, for example:
   - `tools/excel_oracle.py` together with `python/tests/test_excel_oracle.py`
   - `python/tests/test_canonical_model.py`
   - `rust/src/model.rs` sha256 chunking on its own, so it can be reviewed
     or reverted separately.
3. Run the checks locally before pushing (GitHub Actions is not being used):
   - `ruff check python tools examples setup.py`
   - `pytest`
   - `cargo test --manifest-path rust/Cargo.toml`
4. Push the branch and open a PR. Put the check results in the PR description.

Keep the untracked `AGENTS.md` and `docs/specs/proposed/` out of those commits.
They are handoff docs, not part of the oracle work. Put them in a separate
commit, or better, a separate PR.

Don't `git add -A` on main, don't force-push, and don't delete gitignored files
(`.venv` and similar).
