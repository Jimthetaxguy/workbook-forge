//! Verified port of `digits_plus_one` from rust/src/lib.rs.
//!
//! This is the carry step inside Workbook Forge's round_to_precision (ROUND,
//! ROUNDUP, ROUNDDOWN, TRUNC): when the upper boundary is built, the kept
//! decimal digits get one added, and
//! 1999 becomes 2000. Original code:
//!
//!     fn digits_plus_one(digits: &[u8]) -> Vec<u8> {
//!         let mut raised = digits.to_vec();
//!         for digit in raised.iter_mut().rev() {
//!             if *digit == 9 { *digit = 0; } else { *digit += 1; return raised; }
//!         }
//!         raised.insert(0, 1);
//!         raised
//!     }
//!
//! The port keeps the same steps, written with an index so Verus can follow
//! it. The input is a Vec instead of a slice.
use vstd::prelude::*;
use vstd::arithmetic::power::*;
use vstd::arithmetic::mul::*;

verus! {

/// The number the digits spell, most significant digit first.
pub open spec fn value(s: Seq<u8>) -> int
    decreases s.len(),
{
    if s.len() == 0 { 0 } else { value(s.drop_last()) * 10 + s.last() as int }
}

pub open spec fn all_digits(s: Seq<u8>) -> bool {
    forall|i: int| 0 <= i < s.len() ==> #[trigger] s[i] <= 9
}

/// value(a + b) == value(a) * 10^|b| + value(b)
proof fn lemma_value_append(a: Seq<u8>, b: Seq<u8>)
    ensures value(a + b) == value(a) * pow(10, b.len() as nat) + value(b),
    decreases b.len(),
{
    reveal(pow);
    if b.len() == 0 {
        assert(a + b =~= a);
    } else {
        let b0 = b.drop_last();
        assert((a + b).drop_last() =~= a + b0);
        assert((a + b).last() == b.last());
        lemma_value_append(a, b0);
        lemma_mul_is_associative(value(a), pow(10, b0.len() as nat), 10);
        lemma_mul_is_commutative(pow(10, b0.len() as nat), 10);
        lemma_mul_is_distributive_add_other_way(10, value(a) * pow(10, b0.len() as nat), value(b0));
    }
}

/// k nines plus one is 10^k, and k zeros are zero.
proof fn lemma_nines_zeros(k: nat)
    ensures
        value(Seq::new(k, |_i: int| 9u8)) + 1 == pow(10, k),
        value(Seq::new(k, |_i: int| 0u8)) == 0,
    decreases k,
{
    reveal(pow);
    if k > 0 {
        assert(Seq::new(k, |_i: int| 9u8).drop_last() =~= Seq::new((k - 1) as nat, |_i: int| 9u8));
        assert(Seq::new(k, |_i: int| 0u8).drop_last() =~= Seq::new((k - 1) as nat, |_i: int| 0u8));
        lemma_nines_zeros((k - 1) as nat);
        lemma_mul_is_commutative(10, pow(10, (k - 1) as nat));
    }
}

/// Add one to a number written as decimal digits.
/// Guarantee: the result spells exactly one more than the input, every
/// element is still a digit 0..9, and it grows by at most one digit.
pub fn digits_plus_one(digits: &Vec<u8>) -> (raised: Vec<u8>)
    requires
        all_digits(digits@),
        digits.len() < usize::MAX,
    ensures
        all_digits(raised@),
        value(raised@) == value(digits@) + 1,
        raised.len() == digits.len() || raised.len() == digits.len() + 1,
{
    let mut raised = digits.clone();
    let n = raised.len();
    let mut i = n;
    // Walk from the last digit toward the first. Everything at or after i
    // was a 9 and is now a 0; everything before i is untouched.
    while i > 0
        invariant
            0 <= i <= n,
            n == digits.len(),
            raised.len() == n,
            all_digits(digits@),
            raised@.subrange(0, i as int) == digits@.subrange(0, i as int),
            digits@.subrange(i as int, n as int) == Seq::new((n - i) as nat, |_j: int| 9u8),
            raised@.subrange(i as int, n as int) == Seq::new((n - i) as nat, |_j: int| 0u8),
        decreases i,
    {
        let j = i - 1;
        let ghost before = raised@;
        proof {
            // Position j is still the original digit.
            assert(before.subrange(0, i as int)[j as int] == before[j as int]);
            assert(digits@.subrange(0, i as int)[j as int] == digits@[j as int]);
            assert(before[j as int] == digits@[j as int]);
            assert forall|k: int| i <= k < n implies #[trigger] digits@[k] == 9u8 && before[k] == 0u8 by {
                assert(digits@.subrange(i as int, n as int)[k - i] == digits@[k]);
                assert(before.subrange(i as int, n as int)[k - i] == before[k]);
            }
            assert forall|k: int| 0 <= k < j implies #[trigger] before[k] == digits@[k] by {
                assert(before.subrange(0, i as int)[k] == before[k]);
                assert(digits@.subrange(0, i as int)[k] == digits@[k]);
            }
        }
        if raised[j] == 9 {
            raised.set(j, 0);
            proof {
                assert(raised@ == before.update(j as int, 0u8));
                assert forall|k: int| j <= k < n implies #[trigger] raised@[k] == 0u8 by { if k > j { assert(digits@[k] == 9u8); } }
                assert(digits@.subrange(j as int, n as int) =~= Seq::new((n - j) as nat, |_k: int| 9u8));
                assert(raised@.subrange(j as int, n as int) =~= Seq::new((n - j) as nat, |_k: int| 0u8));
                assert(raised@.subrange(0, j as int) =~= digits@.subrange(0, j as int));
            }
            i = j;
        } else {
            let d = raised[j];
            assert(d == digits@[j as int]);
            raised.set(j, d + 1);
            proof {
                let pre = digits@.subrange(0, j as int);
                let k = (n - i) as nat;
                let nines = Seq::new(k, |_x: int| 9u8);
                let zeros = Seq::new(k, |_x: int| 0u8);
                assert(digits@ =~= pre.push(d) + nines);
                assert(raised@ =~= pre.push((d + 1) as u8) + zeros);
                lemma_value_append(pre.push(d), nines);
                lemma_value_append(pre.push((d + 1) as u8), zeros);
                assert(pre.push(d).drop_last() =~= pre);
                assert(pre.push((d + 1) as u8).drop_last() =~= pre);
                lemma_nines_zeros(k);
                lemma_mul_is_distributive_add_other_way(pow(10, k), value(pre.push(d)), 1);
                assert(all_digits(raised@));
            }
            return raised;
        }
    }
    // Every digit was a 9: 999 becomes 1000.
    proof {
        assert(digits@.subrange(0, n as int) =~= digits@);
        assert(raised@.subrange(0, n as int) =~= raised@);
    }
    raised.insert(0, 1);
    proof {
        let nines = Seq::new(n as nat, |_x: int| 9u8);
        let zeros = Seq::new(n as nat, |_x: int| 0u8);
        assert(digits@ =~= nines);
        assert(raised@ =~= seq![1u8] + zeros);
        lemma_value_append(seq![1u8], zeros);
        assert(seq![1u8].drop_last() =~= Seq::<u8>::empty());
        assert(value(seq![1u8]) == 1) by { reveal_with_fuel(value, 2); }
        lemma_nines_zeros(n as nat);
        lemma_mul_basics(pow(10, n as nat));
        assert(all_digits(raised@));
    }
    raised
}

/// Sanity check: 1999 cents carries to 2000.
fn example_call() {
    let mut v: Vec<u8> = Vec::new();
    v.push(1); v.push(9); v.push(9); v.push(9);
    let r = digits_plus_one(&v);
    assert(value(v@) == 1999) by {
        assert(v@ =~= seq![1u8, 9u8, 9u8, 9u8]);
        reveal_with_fuel(value, 5);
    }
    assert(value(r@) == 2000);
}

} // verus!
