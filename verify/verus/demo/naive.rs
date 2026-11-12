//! The "obvious" versions, with the same contracts. Verus rejects them.
//! This file is NOT part of the verified crate. `bash demo.sh` runs it and the
//! expected output is saved in demo/naive-output.txt.
use vstd::prelude::*;

verus! {

pub open spec fn sum(s: Seq<u64>) -> int
    decreases s.len(),
{
    if s.len() == 0 { 0 } else { sum(s.drop_last()) + s.last() as int }
}

/// Textbook round-half-up: add half a step, then cut. Looks fine, passes
/// every unit test with normal amounts. It overflows near u64::MAX: in a
/// release build the add wraps silently and the answer is near zero.
pub fn naive_round_nearest(magnitude: u64, place: u64) -> (r: u64)
    requires place > 0,
{
    (magnitude + place / 2) / place * place
}

/// Textbook even split: everyone gets total / n. Never overflows, but the
/// parts do not add back to the total: $100.00 three ways is 33.33 x 3 = 99.99.
pub fn naive_split(total: u64, n: u64) -> (parts: Vec<u64>)
    requires n > 0, n < 1000,
    ensures parts.len() == n, sum(parts@) == total as int,
{
    let mut parts: Vec<u64> = Vec::new();
    let mut i: u64 = 0;
    while i < n
        invariant i <= n, parts.len() == i,
        decreases n - i,
    {
        parts.push(total / n);
        i = i + 1;
    }
    parts
}

} // verus!
