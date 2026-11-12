# Verus proofs for Workbook Forge money math

This folder proves, before anything runs, that three pieces of money math
can never overflow, never wrap, and never lose or invent a cent. The proofs
cover every possible input, not a sample of test cases. Verus checks them at
build time and adds nothing at runtime.

## What is proved

| File | What it is | What Verus proves for every input |
|---|---|---|
| `src/round_to_place.rs` | Port of the integer core of `round_to_precision` in `rust/src/lib.rs` (ROUND, ROUNDUP, ROUNDDOWN, TRUNC). | The result is exactly the math answer: a whole number of steps, less than one step from the input, never below the input for round-up, never above it for round-down, at most half a step away for nearest with halves going away from zero. If the answer would not fit in a u64 it returns `None`. It never wraps. |
| `src/digits_plus_one.rs` | Port of `digits_plus_one` in `rust/src/lib.rs`, the carry step when a rounded number goes up (1999 to 2000). | The output spells exactly one more than the input, every element is still a digit 0 to 9, and it grows by at most one digit. |
| `src/allocate.rs` | New code, not a port. Workbook Forge has no allocation helper today. Spreads one amount across weighted lines. | The parts add back to the total exactly. Each part is within one unit (one cent if you pass cents) of its exact share. A line with weight zero gets zero. No step overflows: the products are done in 128 bits and that is proved to be enough. |

Each file ends with an example call (for example "1999 carries to 2000" and
"10000 cents three ways adds to 10000"). Verus proves those too, which shows
the preconditions can actually be met and the contracts are not empty
promises.

What "port" means here: the Forge code rounds a binary64 float by writing it
out as decimal digits. Verus cannot reason about that float-to-text step, so
the rounding port works on whole units (cents, tenths of a cent) and leaves
out the "boundary steps" snap, which only exists for floats. The carry
function is the same steps as the original, written with an index instead of
`iter_mut().rev()`. The original Forge code is unchanged by this PR.

## Run it

```bash
bash verify.sh
```

Real output from the box on 2026-11-12, run from this folder (also in `verify-output.txt`):

```
cheat-check: OK (no assume, admit, external_body or no-decreases escape hatches in src/)
verification results:: 19 verified, 0 errors
```

## See the value: the obvious code fails

```bash
bash demo.sh
```

`demo/naive.rs` has the textbook versions with the same kind of contract.
Verus rejects both (full output in `demo/naive-output.txt`):

```
error: possible arithmetic underflow/overflow
20 |     (magnitude + place / 2) / place * place
error: postcondition not satisfied
27 |     ensures parts.len() == n, sum(parts@) == total as int,
verification results:: 2 verified, 2 errors
```

Verus reports the overflow line twice. The first is the real bug: adding half
a step can pass u64::MAX. The second (`/ place * place`) cannot actually
overflow; Verus just needs a hint to see that, which is the "nonlinear
arithmetic" limit below.

The same bugs at runtime, plain Rust in a release build
(`demo/wrap_at_runtime.rs`, output in `demo/wrap-output.txt`):

```
naive_round_nearest(18446744073709551605, 100) = 0
naive split of 10000 cents three ways: [3333, 3333, 3333], sum = 9999
```

Unit tests with normal amounts pass on both naive versions. Only the proof
finds them.

## Install

Uses the official prebuilt release, nothing built from source.

```bash
# Verus 0.2026.11.02.8bf68ee, x86 Linux (pick your platform's zip on the releases page)
mkdir -p ~/tools/verus && cd ~/tools/verus
curl -sSLO https://github.com/verus-lang/verus/releases/download/release%2F0.2026.11.02.8bf68ee/verus-0.2026.11.02.8bf68ee-x86-linux.zip
unzip verus-0.2026.11.02.8bf68ee-x86-linux.zip
# Verus needs this exact Rust toolchain; it tells you if it is missing
rustup install --profile minimal 1.99.1
```

`verify.sh` looks for `verus` on PATH, then `~/tools/verus/verus-x86-linux/verus`,
or set `VERUS=/path/to/verus`. On a Mac use the arm64-macos zip and point
`VERUS` at it.

## Known limits

- A proof is only as good as the contract. A wrong or too-loose `ensures`
  proves the wrong thing, and a precondition that can never be true proves
  anything. A person still has to read the contracts. The example calls
  guard against the second trap. Each contract was also mutation tested: making a
  promise slightly stronger than the truth (sum is total plus one, or no
  slack on the per-line share) makes Verus fail, so the contracts are tight.
- Nonlinear arithmetic (multiplying two variables, dividing by a variable)
  is where Verus needs help. The allocation proof leans on `vstd` lemmas for
  this. Proofs like these can get slow or flaky as they grow, so keep each
  proved function small.
- Floats are out of scope. The proofs are about whole units. Converting a
  float cell to cents is a separate step that is not proved here.
- This is not wired into CI or into the Forge engine. It is a standalone
  proof crate next to the code.

## How a coding agent can use this loop

1. A person (or an architect agent) writes the contract: the `requires`,
   `ensures` and `spec fn` parts. That is the locked part. An agent may not
   edit it.
2. The coding agent writes or changes the function body and the proof hints
   (`proof { ... }` blocks, lemmas, loop invariants).
3. It runs `bash verify.sh` and reads the Verus errors, which point at the exact
   line and the exact promise that failed.
4. It repeats until both checks pass: Verus reports `0 errors` and the cheat
   check reports `OK`.
5. Review is then small: confirm the contract lines did not change
   (`git diff` on the `requires`, `ensures` and `spec fn` lines) and that the
   cheat check ran.

The cheat check stops the usual shortcuts: `assume(`, `admit(`,
`#[verifier::external_body]`, `#[verifier::external]`,
`#[verifier::exec_allows_no_decreases_clause]` and `assume_specification`.
A useful next step is a script that hashes the contract lines so step 5 is
automatic too.

Background: the Verus guide on using LLMs for proofs
(https://verus-lang.github.io/verus/guide/llmforverusproof.html) and the
SeVerus benchmark (https://arxiv.org/abs/2611.07194), which found agents
finish proofs well when given helper lemmas and struggle to invent them.
That is why the lemmas here are kept as separate, reusable pieces.
