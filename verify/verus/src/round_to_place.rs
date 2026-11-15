//! Verified port of the integer core of `round_to_precision` in rust/src/lib.rs.
//!
//! Workbook Forge rounds the magnitude of a number at a power-of-ten place
//! and then puts the sign back. Here the magnitude is a whole number of the
//! smallest unit (for example cents) and `place` is how many of those units
//! make one step (100 cents for whole dollars). The three modes are the ones
//! the Forge code passes as strings: "nearest" (halves go away from zero),
//! "away-from-zero" (ROUNDUP) and toward zero (ROUNDDOWN and TRUNC).
//!
//! Not ported: the binary64 "boundary steps" snap, which only exists because
//! the Forge input is a float. On whole units it has nothing to snap.
use vstd::prelude::*;

verus! {

pub enum Mode {
    Nearest,
    AwayFromZero,
    TowardZero,
}

/// The rounded value as plain math on unbounded integers. This is the
/// contract: the code below must produce exactly this number.
pub open spec fn spec_round(magnitude: int, place: int, mode: Mode) -> int
    recommends place > 0, magnitude >= 0,
{
    let rem = magnitude % place;
    let lower = magnitude - rem;
    if rem == 0 {
        magnitude
    } else {
        match mode {
            Mode::Nearest => if 2 * rem >= place { lower + place } else { lower },
            Mode::AwayFromZero => lower + place,
            Mode::TowardZero => lower,
        }
    }
}

/// What the contract promises, stated as properties a reviewer can check:
/// the result is a whole number of steps, it moves less than one step, it
/// moves the right way for the mode, and nearest moves at most half a step.
pub proof fn lemma_round_properties(magnitude: int, place: int, mode: Mode)
    requires
        place > 0,
        magnitude >= 0,
    ensures
        spec_round(magnitude, place, mode) % place == 0,
        spec_round(magnitude, place, mode) >= 0,
        magnitude - place < spec_round(magnitude, place, mode) < magnitude + place,
        mode is TowardZero ==> spec_round(magnitude, place, mode) <= magnitude,
        mode is AwayFromZero ==> spec_round(magnitude, place, mode) >= magnitude,
        mode is Nearest ==> 2 * (spec_round(magnitude, place, mode) - magnitude) <= place,
        mode is Nearest ==> 2 * (magnitude - spec_round(magnitude, place, mode)) < place,
{
    let rem = magnitude % place;
    let q = magnitude / place;
    vstd::arithmetic::div_mod::lemma_fundamental_div_mod(magnitude, place);
    vstd::arithmetic::div_mod::lemma_mod_bound(magnitude, place);
    vstd::arithmetic::div_mod::lemma_mod_multiples_basic(q, place);
    vstd::arithmetic::div_mod::lemma_mod_multiples_basic(q + 1, place);
    vstd::arithmetic::mul::lemma_mul_is_distributive_add_other_way(place, q, 1);
    assert(q * place + place == (q + 1) * place);
    assert(magnitude - rem == q * place);
}

/// Round a non-negative amount at `place` units. Returns None only when the
/// rounded value does not fit in a u64, so it can never overflow or wrap.
pub fn round_to_place(magnitude: u64, place: u64, mode: Mode) -> (r: Option<u64>)
    requires
        place > 0,
    ensures
        r is Some ==> r->Some_0 == spec_round(magnitude as int, place as int, mode),
        r is None ==> spec_round(magnitude as int, place as int, mode) > u64::MAX,
{
    let rem = magnitude % place;
    proof {
        // rem is what is left after whole steps, so it is never above magnitude.
        vstd::arithmetic::div_mod::lemma_mod_decreases(magnitude as nat, place as nat);
    }
    let lower = magnitude - rem;
    if rem == 0 {
        return Some(magnitude);
    }
    let up = match mode {
        // 2 * rem >= place, written so it cannot overflow.
        Mode::Nearest => rem >= place - rem,
        Mode::AwayFromZero => true,
        Mode::TowardZero => false,
    };
    if !up {
        Some(lower)
    } else if lower > u64::MAX - place {
        None
    } else {
        Some(lower + place)
    }
}

/// Sanity check that the precondition can be met and the contract is not
/// vacuous: concrete calls whose results Verus must prove.
fn example_calls() {
    // $12.345 in tenths of a cent, to the cent: 12345 -> 12350 (half goes up).
    let a = round_to_place(12345, 10, Mode::Nearest);
    assert(a == Some(12350u64));
    let b = round_to_place(12344, 10, Mode::Nearest);
    assert(b == Some(12340u64));
    // TRUNC and ROUNDUP to whole dollars on 1999 cents.
    let c = round_to_place(1999, 100, Mode::TowardZero);
    assert(c == Some(1900u64));
    let d = round_to_place(1901, 100, Mode::AwayFromZero);
    assert(d == Some(2000u64));
    // The largest u64 cannot be rounded up to a whole dollar: None, not a wrap.
    let e = round_to_place(u64::MAX, 100, Mode::AwayFromZero);
    assert(e is None);
}

} // verus!
