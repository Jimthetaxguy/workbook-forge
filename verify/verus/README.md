# Verus proofs for Workbook Forge money math

This folder proves three pieces of money math can never overflow, never wrap,
and never lose or invent a cent, for every input that satisfies each
function's `requires` clause. Verus reasons about all such inputs
symbolically instead of testing a sample. The proof code is erased, so it
adds nothing at runtime.

## What is proved

| File | What it is | What Verus proves for every input that meets `requires` |
|---|---|---|
| `src/round_to_place.rs` | Port of the integer core of `round_to_precision` in `rust/src/lib.rs` (ROUND, ROUNDUP, ROUNDDOWN, TRUNC). | The result is exactly the math answer: a whole number of steps, less than one step from the input, never below the input for round-up, never above it for round-down, at most half a step away for nearest with halves going away from zero. If the answer would not fit in a u64 it returns `None` instead of wrapping. |
| `src/digits_plus_one.rs` | Port of `digits_plus_one` in `rust/src/lib.rs`, the carry step when ROUND builds its upper value (1999 to 2000). | The output spells exactly one more than the input, every element is still a digit 0 to 9, and it grows by at most one digit. |
| `src/allocate.rs` | New code, not a port. Workbook Forge has no allocation helper today. Spreads one amount across weighted lines. | The parts add back to the total exactly. Each part is within one unit (one cent if you pass cents) of its exact share. A line with weight zero gets zero. The products are done in 128 bits and that is proved to be enough. |

Each file ends with an example call (for example "1999 carries to 2000" and
"10000 cents three ways adds to 10000"). Verus proves those too, which shows
the preconditions can actually be met, so the contracts are not vacuous.

What "port" means here: the Forge code rounds a binary64 float by writing it
out as decimal digits. Verus cannot reason about that float-to-text step, so
the rounding port works on whole units (cents, tenths of a cent) and leaves
out the "boundary steps" snap, which only exists for floats. The carry
function is the same steps as the original, written with an index instead of
`iter_mut().rev()`. The original Forge code is unchanged by this PR.

## What a proof does and does not give you

- **Only inputs that satisfy `requires`.** Verus checks, at each verified
  call site, that the caller meets the precondition. Inputs outside it are
  not covered.
- **Contracts are not runtime guards.** `requires` and `ensures` are checked
  by Verus before the program runs and then erased. A normal `cargo build`
  or `rustc` build ignores them and gets no protection. Unverified callers
  (for example the Python engine, or Rust that never goes through Verus) can
  still pass bad inputs.
- **Right rule, not right law.** Verus proves the code follows the rule
  written in the contract. It does not prove that rule is the correct tax or
  accounting interpretation. That is still a human call.
- **Trusted base.** The result is only as sound as Verus itself, the Z3
  solver it calls, rustc (which compiles the code Verus checked) and the
  hardware. Exact versions are pinned in `toolchain.lock`.

## Run it

```bash
bash verify.sh
```

`verify.sh` runs four checks in order and stops at the first failure:

1. **Toolchain pin.** Refuses to run unless Verus, rustc and Z3 match
   `toolchain.lock` exactly.
2. **Spec lock** (`spec_lock.py`). Structural check, see below.
3. **Text scan** (`cheat-check.sh`). A plain grep for escape hatches, kept as
   a second layer.
4. **Verus** on every proof in `src/`.

Real output from the box on 2026-11-12 (also in `verify-output.txt`):

```
toolchain: OK (Verus 0.2026.11.02.8bf68ee, rustc 1.99.1 (58cf8f9c3 2026-10-13), Z3 version 4.16.0 - 64 bit)
spec-lock: OK (27 locked items match spec.lock, no escape hatches)
cheat-check: OK (no assume, admit, axiom, external_body, external specs or no-decreases escape hatches in src/)
verification results:: 19 verified, 0 errors
```

With a different Verus pinned (output in `demo/toolchain-mismatch-output.txt`):

```
toolchain: Verus is '0.2026.11.02.8bf68ee', toolchain.lock wants '0.2026.11.05.31e44e6'
toolchain: MISMATCH, refusing to run. Install the pinned versions (README.md, Install).
```

## The spec lock

A text scan alone is not enough: it cannot see a weakened `ensures`.
`spec_lock.py` parses the files in `src/` (comments and strings stripped) and
hashes, into `spec.lock`:

- every fn header: the signature plus `requires`, `ensures`, `recommends`
  and `decreases`, for exec, proof and spec fns;
- the full body of every spec fn, since the contracts are written in them;
- the full body of every `example_*` fn, the non-vacuity checks;
- every type definition in the proof files;
- every trusted marker: `#[verifier::...]` attributes (except solver tuning
  such as `rlimit`) and uninterpreted spec fns.

`python3 spec_lock.py check` fails if any locked item changes, disappears or
appears unapproved, if unlocked trusted code shows up, and always on
`assume(`, `admit(`, axioms, `external_body`, `external`,
`external_fn_specification`, `assume_specification` or
`exec_allows_no_decreases_clause`. Exec fn bodies and proof fn bodies stay
editable, because that is the agent's job. After a person reviews a contract
change, `python3 spec_lock.py approve` rewrites `spec.lock`, and that change
shows in the diff.

`bash demo/spec_lock_catches.sh` plants patches in a scratch copy. Real
output (full text in `demo/spec-lock-output.txt`):

```
=== 1. weakened ensures: parts only need to sum to AT MOST the total
--- spec lock:
locked item changed: contract allocate.rs::allocate
spec-lock: FAIL (1 problem(s)). ...
--- grep layer:
cheat-check: OK (...)                      <- the text scan misses it

=== 2. added assume inside a proof block
--- spec lock:
round_to_place.rs:80: banned escape hatch assume(
spec-lock: FAIL (1 problem(s)). ...
--- grep layer:
src/round_to_place.rs:80:        assume(false);
cheat-check: FAIL (escape hatch found above)

=== 3. added external_body on the allocator
--- spec lock:
allocate.rs:89: banned escape hatch #[verifier::external_body]
locked item changed: contract allocate.rs::allocate
unlocked trusted code: trusted allocate.rs::allocate #[verifier::external_body]
spec-lock: FAIL (3 problem(s)). ...

=== 4. allowed: an extra proof hint in an exec body (no contract change)
--- spec lock:
spec-lock: OK (27 locked items match spec.lock, no escape hatches)
```

Limits of the lock: it is a careful parser in about 200 lines of Python,
not the Rust compiler's parser. It hashes contract text, so a pure
formatting change inside a contract is ignored (whitespace is normalized)
but a renamed variable counts as a change. It does not see code outside
`src/`, so keep every verified file there.

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
overflow; Verus needs a hint to see that, which is the nonlinear arithmetic
limit below.

What the bug does at runtime (`demo/wrap_at_runtime.rs`, output in
`demo/wrap-output.txt`). Rust's default release profile turns overflow checks
off, so `+` wraps there. That is a default, not a rule: debug builds panic
instead, and a profile can set `overflow-checks = true`. The demo uses
`wrapping_add` to show the release default explicitly:

```
naive_round_nearest(18446744073709551605, 100) = 0
naive split of 10000 cents three ways: [3333, 3333, 3333], sum = 9999
```

Unit tests with normal amounts pass on both naive versions.

## Install

Uses the official prebuilt release, nothing built from source. Versions must
match `toolchain.lock`.

```bash
mkdir -p ~/tools/verus && cd ~/tools/verus
curl -sSLO https://github.com/verus-lang/verus/releases/download/release%2F0.2026.11.02.8bf68ee/verus-0.2026.11.02.8bf68ee-x86-linux.zip
sha256sum verus-0.2026.11.02.8bf68ee-x86-linux.zip   # compare with toolchain.lock
unzip verus-0.2026.11.02.8bf68ee-x86-linux.zip
rustup install --profile minimal 1.99.1
```

`verify.sh` looks for `verus` on PATH, then `~/tools/verus/verus-x86-linux/verus`,
or set `VERUS=/path/to/verus`. On a Mac use the arm64-macos zip of the same
release; Z3 is bundled in the zip.

## Known limits

- A proof is only as good as the contract. A too-loose `ensures` lets wrong
  code pass, and a precondition no caller can meet proves anything. A person
  still reads the contracts. The example calls guard against the second trap,
  and each contract was mutation tested: making a promise slightly stronger
  than the truth (sum is total plus one, no slack on the per-line share)
  makes Verus fail.
- Nonlinear arithmetic (multiplying two variables, dividing by a variable)
  is where the solver gets unpredictable. These proofs lean on `vstd` lemmas
  and one `by (nonlinear_arith)` step. Verus also offers `by (bit_vector)`
  for bit-level facts; none is needed here. Quantifier proofs depend on
  triggers and solver behavior, so keep proved functions small.
- Floats are out of scope. Converting a float cell to whole cents is a
  separate step that is not proved here.
- Not wired into CI or into the Forge engine. It is a standalone proof
  crate next to the code.
- Keep ordinary tests and fuzzing for integration behavior.

## How a coding agent can use this loop

1. A person (or an architect agent) writes the contract: `requires`,
   `ensures`, spec fns and example calls, then runs
   `python3 spec_lock.py approve`. That is the locked part.
2. The coding agent edits only exec bodies and proof hints (`proof { ... }`
   blocks, lemma bodies, loop invariants).
3. It runs `bash verify.sh` and reads the Verus errors, which point at the
   exact line and the exact promise that failed.
4. It repeats until all four checks pass.
5. Any change to `spec.lock` or `toolchain.lock` in the diff means a
   contract or trust change and goes back to a person.

Background: the Verus guide on using LLMs for proofs
(https://verus-lang.github.io/verus/guide/llmforverusproof.html) and the
SeVerus benchmark (https://arxiv.org/abs/2611.07194). SeVerus uses an
independent checker to reject code changes, weakened specs and unsound
proof constructs, and found agents solve far fewer levels once helper lemmas
are removed. That is why the lemmas here are separate, reusable pieces.
