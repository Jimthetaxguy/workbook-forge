// Plain Rust, no Verus. wrapping_add is what a plain + does in a release
// build (overflow checks off), written out so the compiler cannot refuse it:
//   rustc -O demo/wrap_at_runtime.rs -o /tmp/wrap && /tmp/wrap
#[inline(never)]
fn naive_round_nearest(magnitude: u64, place: u64) -> u64 {
    magnitude.wrapping_add(place / 2) / place * place
}
fn main() {
    let big = u64::MAX - 10;
    let place = std::hint::black_box(100u64);
    println!("naive_round_nearest({big}, 100) = {}", naive_round_nearest(big, place));
    println!("expected: 18446744073709551600 (or an explicit 'does not fit')");
    let total = 10_000u64;
    let parts = [total / 3; 3];
    println!("naive split of 10000 cents three ways: {:?}, sum = {}", parts, parts.iter().sum::<u64>());
}
