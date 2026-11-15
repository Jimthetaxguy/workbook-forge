//! Verified cent allocation: split a total across weighted lines so the parts
//! tie out to the total exactly, with no cent created or lost.
//!
//! This is NEW code, not a port. Workbook Forge has no allocation helper
//! today. It is the piece a tax workpaper needs when one amount (a book-tax
//! adjustment, an apportioned expense) is spread across entities or accounts
//! by weight, and the spread lines must foot to the original amount.
//!
//! Method: cumulative rounding. Line i gets
//!     floor(total * C_i / W) - floor(total * C_(i-1) / W)
//! where C_i is the running sum of weights and W the sum of all weights.
//! The parts telescope, so they always add back to floor(total * W / W) = total.
use vstd::prelude::*;
use vstd::arithmetic::div_mod::*;
use vstd::arithmetic::mul::*;

verus! {

/// Sum of a sequence of u64, as an unbounded integer.
pub open spec fn sum(s: Seq<u64>) -> int
    decreases s.len(),
{
    if s.len() == 0 { 0 } else { sum(s.drop_last()) + s.last() as int }
}

/// Running sum of the first n weights.
pub open spec fn prefix(s: Seq<u64>, n: int) -> int {
    sum(s.subrange(0, n))
}

proof fn lemma_prefix_step(s: Seq<u64>, n: int)
    requires 0 <= n < s.len(),
    ensures prefix(s, n + 1) == prefix(s, n) + s[n] as int,
{
    assert(s.subrange(0, n + 1).drop_last() =~= s.subrange(0, n));
}

proof fn lemma_prefix_bounds(s: Seq<u64>, n: int)
    requires 0 <= n <= s.len(),
    ensures 0 <= prefix(s, n) <= sum(s),
    decreases s.len() - n,
{
    if n < s.len() {
        lemma_prefix_step(s, n);
        lemma_prefix_bounds(s, n + 1);
    } else {
        assert(s.subrange(0, n) =~= s);
    }
    lemma_prefix_nonneg(s, n);
}

proof fn lemma_prefix_nonneg(s: Seq<u64>, n: int)
    requires 0 <= n <= s.len(),
    ensures prefix(s, n) >= 0,
    decreases n,
{
    if n > 0 {
        lemma_prefix_step(s, n - 1);
        lemma_prefix_nonneg(s, n - 1);
    }
}

/// floor(a / w) and floor(b / w) differ from (b - a) / w by less than one:
/// |(b/w - a/w) * w - (b - a)| < w. This is the "within one cent" fact.
proof fn lemma_floor_diff(a: int, b: int, w: int)
    requires 0 <= a <= b, w > 0,
    ensures
        a / w <= b / w,
        (b / w - a / w) * w > (b - a) - w,
        (b / w - a / w) * w < (b - a) + w,
{
    lemma_div_is_ordered(a, b, w);
    lemma_fundamental_div_mod(a, w);
    lemma_fundamental_div_mod(b, w);
    lemma_mod_bound(a, w);
    lemma_mod_bound(b, w);
    lemma_mul_is_distributive_sub_other_way(w, b / w, a / w);
    lemma_mul_is_commutative(w, b / w);
    lemma_mul_is_commutative(w, a / w);
    lemma_mul_is_commutative(w, b / w - a / w);
}

/// Split `total` across `weights`. Guarantees, for every possible input that
/// meets the precondition:
///   - one part per weight, and the parts add up to `total` exactly;
///   - each part is within one unit of its exact share:
///     |part_i * W - total * w_i| < W;
///   - a line with weight zero gets zero.
pub fn allocate(total: u64, weights: &Vec<u64>) -> (parts: Vec<u64>)
    requires
        sum(weights@) > 0,
        sum(weights@) <= u64::MAX,
    ensures
        parts.len() == weights.len(),
        sum(parts@) == total as int,
        forall|i: int| 0 <= i < parts.len() ==>
            #[trigger] parts[i] as int * sum(weights@) > total as int * weights[i] as int - sum(weights@)
            && parts[i] as int * sum(weights@) < total as int * weights[i] as int + sum(weights@),
        forall|i: int| 0 <= i < parts.len() && #[trigger] weights[i] == 0 ==> parts[i] == 0,
{
    let ghost ws = weights@;
    let ghost big_w = sum(ws);
    // W, the sum of all weights. Fits in u64 by the precondition.
    let mut w_total: u64 = 0;
    let mut k: usize = 0;
    while k < weights.len()
        invariant
            0 <= k <= weights.len(),
            ws == weights@,
            big_w == sum(ws),
            big_w <= u64::MAX,
            w_total as int == prefix(ws, k as int),
        decreases weights.len() - k,
    {
        proof {
            lemma_prefix_step(ws, k as int);
            lemma_prefix_bounds(ws, k as int + 1);
        }
        w_total = w_total + weights[k];
        k = k + 1;
    }
    proof {
        assert(ws.subrange(0, ws.len() as int) =~= ws);
    }

    let mut parts: Vec<u64> = Vec::new();
    let mut running: u64 = 0;   // C_(i-1)
    let mut given: u64 = 0;     // floor(total * C_(i-1) / W), what is handed out so far
    let mut i: usize = 0;
    while i < weights.len()
        invariant
            0 <= i <= weights.len(),
            ws == weights@,
            big_w == sum(ws),
            0 < big_w <= u64::MAX,
            w_total as int == big_w,
            parts.len() == i,
            running as int == prefix(ws, i as int),
            given as int == (total as int * running as int) / big_w,
            sum(parts@) == given as int,
            forall|j: int| 0 <= j < i ==>
                #[trigger] parts[j] as int * big_w > total as int * ws[j] as int - big_w
                && parts[j] as int * big_w < total as int * ws[j] as int + big_w,
            forall|j: int| 0 <= j < i && #[trigger] ws[j] == 0 ==> parts[j] == 0,
        decreases weights.len() - i,
    {
        let ghost old_parts = parts@;
        proof {
            lemma_prefix_step(ws, i as int);
            lemma_prefix_bounds(ws, i as int + 1);
        }
        let next_running: u64 = running + weights[i];
        let ghost a = total as int * running as int;
        let ghost b = total as int * next_running as int;
        proof {
            lemma_mul_inequality(running as int, next_running as int, total as int);
            lemma_mul_is_commutative(running as int, total as int);
            lemma_mul_is_commutative(next_running as int, total as int);
            lemma_mul_inequality(next_running as int, big_w, total as int);
            lemma_mul_is_commutative(big_w, total as int);
            lemma_mul_upper_bound(total as int, u64::MAX as int, next_running as int, u64::MAX as int);
            assert(u64::MAX as int * u64::MAX as int <= u128::MAX) by (nonlinear_arith);
            lemma_mul_nonnegative(total as int, running as int);
            lemma_floor_diff(a, b, big_w);
            // b <= total * W, so b / W <= total.
            lemma_div_is_ordered(b, total as int * big_w, big_w);
            lemma_div_by_multiple(total as int, big_w);
            lemma_mul_is_distributive_add(total as int, running as int, weights[i as int] as int);
        }
        // The products need 128 bits; they cannot overflow there.
        let numer: u128 = (total as u128) * (next_running as u128);
        let cumulative: u64 = (numer / (w_total as u128)) as u64;
        let part: u64 = cumulative - given;
        proof {
            if ws[i as int] == 0 {
                assert(b == a);
            }
        }
        parts.push(part);
        proof {
            assert(parts@.drop_last() =~= old_parts);
        }
        running = next_running;
        given = cumulative;
        i = i + 1;
    }
    proof {
        assert(ws.subrange(0, ws.len() as int) =~= ws);
        lemma_div_by_multiple(total as int, big_w);
    }
    parts
}

/// Sanity check: the precondition can be met, and the result is pinned down.
fn example_call() {
    let mut w: Vec<u64> = Vec::new();
    w.push(1);
    w.push(1);
    w.push(1);
    assert(w@ =~= seq![1u64, 1u64, 1u64]);
    assert(sum(w@) == 3) by {
        reveal_with_fuel(sum, 4);
    }
    // $100.00 three ways: the parts must add to exactly 10000 cents.
    let parts = allocate(10000, &w);
    assert(parts.len() == 3);
    assert(sum(parts@) == 10000);
}

} // verus!
