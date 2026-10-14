---
type: technical-specification
status: proposed
base: main c84ec07
summary: Evaluate ruff 0.17 against the current rule set and set up a repeatable license review for Rust crate upgrades, fixing the license/license-file Cargo warning.
---

# 04 — Toolchain: ruff 0.17 and crate-upgrade license review

## Problem

- `ruff` is pinned at 0.16.9 (`requirements-dev.lock`). 0.17.0 was deferred.
- No Rust crate can be upgraded without a license-file review, because
  `catalog/dependency-licenses.json` checksums each crate and
  `python/tests/test_project_policy.py` fails on an unreviewed crate. There is
  no written procedure for that review.
- Cargo warns that both `license` and `license-file` are set in
  `rust/Cargo.toml` and `native/Cargo.toml`.

## Outcome

Upgrades become a routine, reviewable change instead of a blocker: one PR to
move ruff if the rule set is unchanged, and a documented, test-backed path for
crate upgrades that keeps the permissive-only policy.

## Scope

1. Ruff: in a branch, install 0.17.0, run
   `ruff check python tools examples setup.py` with the same selected rules
   (E4, E7, E9, F). Record new findings. Only move the pin if findings are zero
   or are fixed with plain expression edits. Update the hash in
   `requirements-dev.lock`.
2. License review procedure: add `tools/review_crate_licenses.py` that, for
   each crate in `rust/Cargo.lock` and `native/Cargo.lock`, compares the
   locked version and license file checksum to `catalog/dependency-licenses.json`
   and prints what changed and each SPDX expression. It reads only; it never
   edits the catalog.
3. Cargo warning: keep one of `license` / `license-file`. Proposed: keep
   `license = "MIT"` and drop `license-file`, since LICENSE is MIT.

## Non-goals

- Actually upgrading crates. This spec only makes upgrades reviewable.
- Expanding the ruff rule set.

## Design

- The accepted SPDX list lives in README.md and `test_project_policy.py`.
  The new tool imports that list from one place rather than copying it.
- No network access in tests; the tool reads lockfiles and the local cargo
  registry cache if present, and says "not available" otherwise.

## Schema / model-version impact

None.

## Acceptance tests

```python
# python/tests/test_project_policy.py
def test_ruff_pin_in_lock_matches_pyproject_dev_extra(): ...
def test_crate_license_review_reports_unreviewed_version(tmp_path): ...
    # a copy of Cargo.lock with one bumped version → tool lists that crate.
def test_crate_license_review_flags_copyleft_spdx(tmp_path): ...
def test_cargo_manifests_set_only_one_license_field(): ...
```

Manual check recorded in the PR: `cargo build --locked` in `rust/` and
`native/` prints no license warning.

## Dependencies

None.

## Open questions for James

- **Decision:** move to ruff 0.17.0 once the rule set is rechecked? (PR #8.)
- Keep `license` or `license-file` in the Cargo manifests?
