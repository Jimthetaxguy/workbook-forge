# Volatile functions and the calculation context

`NOW`, `TODAY`, `RAND`, `RANDBETWEEN` and `RANDARRAY` can change without an
edit. Workbook Forge keeps them deterministic and replayable by giving every
recalculation one **calculation context**:

| Field | Meaning |
| --- | --- |
| `now_ms` | The instant the pass captured, Unix epoch milliseconds (UTC). |
| `utc_offset_minutes` | The session's offset from UTC, minutes east (`-300` is UTC-05:00), within ±14:00. |
| `seed` | The pass's random seed, an unsigned 64-bit integer written as a decimal string. |

The Python (`workbook_forge/volatile.py`) and Rust (`rust/src/volatile.rs`)
engines implement the same rules independently.

## One context per pass

Each `calculate` (toolkit sessions, the canonical model, the browser command
`applyAndCalculate`) captures one context and every formula in the pass reads
it, so every `NOW()` in one recalculation agrees, also across cells and worker
threads. A toolkit calculation report carries the context as `calc` when the
pass evaluated a volatile formula; a report without volatile formulas is
unchanged. The canonical calculation report does not carry the pass context;
the toolkit report and the event log do.

- `NOW()` = `25569 + (now_ms + utc_offset_minutes·60000) / 86400000`, the
  1900-system serial with the time of day as a fraction (25569 is
  1970-01-01).
- `TODAY()` = `25569 + floor((now_ms + utc_offset_minutes·60000) / 86400000)`,
  computed on integers so the last millisecond before local midnight stays on
  its own day.
- A result outside the 1900 date system (before serial 0 or after
  9999-12-31) is `#NUM!`. The 1904 date system is still refused at intake
  exactly as before; these functions do not change that.

## Time zone

The session offset defaults to **UTC (0)**. A headless caller sets it with
`Session.set_utc_offset_minutes` (Rust) or `Session.utc_offset_minutes`
(Python), or passes a context. The browser worker reads the user's IANA zone
from `Intl.DateTimeFormat().resolvedOptions().timeZone`; the engine resolves
that zone to its current offset with `Intl` before every command and calls
`setUtcOffsetMinutes`, so a daylight-saving change is followed. The Rust
engine itself holds no time-zone database: it only ever sees an offset.

## Random streams

A draw is a pure function of the context and its position:

```
z = seed XOR fnv1a64(utf8(cell key))           # cell key: canonical "Sheet!A1"
z = splitmix64(z + 0x9E3779B97F4A7C15 · (site + 1))
z = splitmix64(z + 0x9E3779B97F4A7C15 · (draw + 1))   # all mod 2^64
u = (z >> 11) · 2^-53                           # uniform in [0, 1)
```

`site` is the RAND-family call's ordinal in the formula's source text (0 for
the first call), and `draw` counts values within one call (`RANDARRAY` fills
row-major). A draw therefore does not depend on evaluation order, laziness or
the worker count.

## Random functions

- `RAND()` is the call's first draw.
- `RANDBETWEEN(bottom, top)` rounds `bottom` up and `top` down to integers and
  returns `bottom + floor(u·(top − bottom + 1))`. No integer between them is
  `#NUM!`; text that is not a number is `#VALUE!`; errors propagate.
- `RANDARRAY([rows], [columns], [min], [max], [whole_number])` defaults to
  1, 1, 0, 1, FALSE and returns a bounded array (no worksheet spill, as for
  `SEQUENCE`). Dimensions truncate and must be positive (`#NUM!` otherwise,
  the `SEQUENCE` profile), within the 100,000-cell array cap; `min > max` is
  `#VALUE!`, and so is a fractional bound with `whole_number` TRUE. Whole
  numbers are `min + floor(u·(max − min + 1))`, others `min + u·(max − min)`.

The rounding of RANDBETWEEN's bounds and the RANDARRAY error codes are
Workbook Forge profiles from Microsoft's documentation; they have not been
spot-checked in Excel.

## Volatility

A formula calling a volatile function is dirty on every pass, and so is
everything that depends on it: incremental calculation recomputes them even
without an edit. Export writes their cached values as usual.

## Overrides for tests and the CLI

`FORGE_CALC_NOW` (Unix milliseconds or an RFC 3339 instant with `Z` or an
explicit offset, such as `2026-01-15T12:00:00Z`), `FORGE_CALC_SEED` (decimal
u64) and `FORGE_CALC_UTC_OFFSET_MINUTES` override the captured clock, seed and
offset. An invalid value is ignored rather than guessed; a local time without
an offset is invalid. The Python test suite pins `FORGE_CALC_NOW` and
`FORGE_CALC_SEED` in `python/tests/conftest.py`. APIs also take an explicit
context: `evaluate(..., context=, cell_key=)`, `python_engine.calculate(...,
context=)`, `Session.calculate(..., context=)`, `model.calculate(...,
context=)` in Python; `evaluate_result_in`, `toolkit::calculate_with`,
`Session::calculate_with` and `model::calculate_with` in Rust. Shared fixture
cases pin theirs with a `context` object (`now_ms`, `utc_offset_minutes`,
`seed`, `cell`).

## Event log and replay

Every `calc` event records the pass context as `calc_context`; when a command
had to calculate its own baseline first, that context is recorded as
`base_calc_context`. Replay reuses both, so the recomputed deltas of volatile
cells equal the logged ones and the replay check stays deterministic. A log
written before this field existed replays as before (it cannot hold volatile
values, which were unsupported).

Undo and redo do not re-roll: undoing an intent recalculates with the context
of the state it returns to (the undone intent's baseline context, or the
calc event before it), and redo uses the context of the intent it restores.
Volatile values in other cells follow that restored clock too.
