//! The calculation context of volatile functions: one captured clock instant,
//! one session UTC offset and one random seed per recalculation.
//!
//! Every `NOW`/`TODAY` in one pass reads the same instant, and every random
//! draw comes from a stream keyed by `(seed, cell key, call site, draw)`, so a
//! pass is reproducible from its context alone and does not depend on the
//! evaluation order or the worker count. The Python engine implements the same
//! rules independently (`workbook_forge/volatile.py`); see
//! `docs/volatile-functions.md`.

use serde::{Deserialize, Deserializer, Serialize, Serializer};
use std::sync::atomic::{AtomicU64, Ordering};

/// Excel 1900-system serial of 1970-01-01.
pub const UNIX_EPOCH_SERIAL: i64 = 25_569;
const MS_PER_DAY: i64 = 86_400_000;
/// Largest accepted session offset: UTC-14:00 .. UTC+14:00.
pub const MAX_UTC_OFFSET_MINUTES: i32 = 14 * 60;
const GOLDEN: u64 = 0x9E37_79B9_7F4A_7C15;

/// Functions whose value can change without an edit. A formula calling one
/// is recalculated on every pass, and so is everything that depends on it.
pub const VOLATILE_FUNCTIONS: &[&str] = &["NOW", "TODAY", "RAND", "RANDBETWEEN", "RANDARRAY"];

/// Random-number functions: each call site in a formula owns one stream.
pub const RANDOM_FUNCTIONS: &[&str] = &["RAND", "RANDBETWEEN", "RANDARRAY"];

/// One recalculation's clock and seed.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CalcContext {
    /// The captured instant, Unix epoch milliseconds (UTC).
    pub now_ms: i64,
    /// Session offset from UTC in minutes, east positive. NOW and TODAY read
    /// the wall clock at this offset.
    pub utc_offset_minutes: i32,
    /// Seed of this pass's random streams, as a decimal string on the wire
    /// (JavaScript numbers cannot carry every u64).
    #[serde(serialize_with = "seed_out", deserialize_with = "seed_in")]
    pub seed: u64,
}

fn seed_out<S: Serializer>(seed: &u64, serializer: S) -> Result<S::Ok, S::Error> {
    serializer.serialize_str(&seed.to_string())
}

fn seed_in<'de, D: Deserializer<'de>>(deserializer: D) -> Result<u64, D::Error> {
    let text = String::deserialize(deserializer)?;
    parse_seed(&text).map_err(serde::de::Error::custom)
}

/// A seed is a decimal u64.
pub fn parse_seed(text: &str) -> Result<u64, String> {
    if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
        return Err("seed must be a decimal integer".into());
    }
    text.parse::<u64>()
        .map_err(|_| "seed must fit in 64 bits".to_string())
}

/// A UTC offset in minutes within +/-14:00.
pub fn check_offset(minutes: i32) -> Result<i32, String> {
    if (-MAX_UTC_OFFSET_MINUTES..=MAX_UTC_OFFSET_MINUTES).contains(&minutes) {
        Ok(minutes)
    } else {
        Err("utc offset must be within -840..=840 minutes".into())
    }
}

static SEED_COUNTER: AtomicU64 = AtomicU64::new(0);

impl CalcContext {
    pub fn new(now_ms: i64, utc_offset_minutes: i32, seed: u64) -> Self {
        Self {
            now_ms,
            utc_offset_minutes,
            seed,
        }
    }

    /// Capture a context for a new pass. `FORGE_CALC_NOW` (Unix ms or an
    /// RFC 3339 instant), `FORGE_CALC_SEED` (decimal u64) and
    /// `FORGE_CALC_UTC_OFFSET_MINUTES` override the clock, the seed and the
    /// session offset. An invalid override is ignored rather than guessed.
    pub fn capture(utc_offset_minutes: i32) -> Self {
        let now_ms = std::env::var("FORGE_CALC_NOW")
            .ok()
            .and_then(|text| parse_instant(&text).ok())
            .unwrap_or_else(|| crate::event_log::clock::wall_ms().floor() as i64);
        let offset = std::env::var("FORGE_CALC_UTC_OFFSET_MINUTES")
            .ok()
            .and_then(|text| text.trim().parse::<i32>().ok())
            .and_then(|minutes| check_offset(minutes).ok())
            .unwrap_or(utc_offset_minutes);
        let seed = std::env::var("FORGE_CALC_SEED")
            .ok()
            .and_then(|text| parse_seed(text.trim()).ok())
            .unwrap_or_else(|| {
                let count = SEED_COUNTER.fetch_add(1, Ordering::Relaxed);
                let mono = crate::event_log::clock::mono_ms().to_bits();
                mix64(
                    (now_ms as u64)
                        ^ mono.rotate_left(17)
                        ^ GOLDEN.wrapping_mul(count.wrapping_add(1)),
                )
            });
        Self::new(now_ms, offset, seed)
    }

    /// Local epoch milliseconds at the session offset.
    fn local_ms(&self) -> i64 {
        self.now_ms
            .saturating_add(i64::from(self.utc_offset_minutes) * 60_000)
    }

    /// `NOW()`: the 1900-system serial with the time of day as a fraction.
    pub fn now_serial(&self) -> f64 {
        UNIX_EPOCH_SERIAL as f64 + self.local_ms() as f64 / MS_PER_DAY as f64
    }

    /// `TODAY()`: the local calendar day's serial, computed on integers so
    /// the last millisecond before local midnight stays on its own day.
    pub fn today_serial(&self) -> i64 {
        UNIX_EPOCH_SERIAL + self.local_ms().div_euclid(MS_PER_DAY)
    }

    /// The `draw`-th uniform value in `[0, 1)` of call site `site` in the
    /// formula at `cell_key` (canonical `Sheet!A1`).
    pub fn uniform(&self, cell_key: &str, site: u64, draw: u64) -> f64 {
        let mut z = self.seed ^ fnv1a64(cell_key.as_bytes());
        z = mix64(z.wrapping_add(GOLDEN.wrapping_mul(site.wrapping_add(1))));
        z = mix64(z.wrapping_add(GOLDEN.wrapping_mul(draw.wrapping_add(1))));
        (z >> 11) as f64 * (1.0 / 9_007_199_254_740_992.0)
    }
}

/// FNV-1a, 64-bit.
pub fn fnv1a64(bytes: &[u8]) -> u64 {
    let mut hash = 0xcbf2_9ce4_8422_2325u64;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

/// The SplitMix64 finalizer.
pub fn mix64(mut z: u64) -> u64 {
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

/// Unix milliseconds from a decimal integer or an RFC 3339 instant with an
/// explicit `Z` or `+HH:MM`/`-HH:MM` offset (`2026-11-03T20:00:00Z`,
/// `2026-11-03T20:00:00.250-05:00`). A local time without an offset is
/// refused: the instant would depend on the machine.
pub fn parse_instant(text: &str) -> Result<i64, String> {
    let text = text.trim();
    let bad = || format!("invalid instant {text:?}; use Unix ms or RFC 3339 with an offset");
    if !text.is_empty()
        && text
            .strip_prefix('-')
            .unwrap_or(text)
            .bytes()
            .all(|b| b.is_ascii_digit())
    {
        return text.parse::<i64>().map_err(|_| bad());
    }
    let bytes = text.as_bytes();
    if bytes.len() < 20 {
        return Err(bad());
    }
    let digits = |range: std::ops::Range<usize>| -> Result<i64, String> {
        let part = text.get(range).ok_or_else(bad)?;
        if part.bytes().all(|b| b.is_ascii_digit()) {
            part.parse::<i64>().map_err(|_| bad())
        } else {
            Err(bad())
        }
    };
    if bytes[4] != b'-'
        || bytes[7] != b'-'
        || !(bytes[10] == b'T' || bytes[10] == b't')
        || bytes[13] != b':'
        || bytes[16] != b':'
    {
        return Err(bad());
    }
    let (year, month, day) = (digits(0..4)?, digits(5..7)?, digits(8..10)?);
    let (hour, minute, second) = (digits(11..13)?, digits(14..16)?, digits(17..19)?);
    let mut index = 19;
    let mut millis = 0i64;
    if bytes.get(index) == Some(&b'.') {
        let start = index + 1;
        let mut end = start;
        while end < bytes.len() && bytes[end].is_ascii_digit() {
            end += 1;
        }
        if end == start {
            return Err(bad());
        }
        let fraction = &text[start..end];
        let padded: String = fraction.chars().chain("000".chars()).take(3).collect();
        millis = padded.parse::<i64>().map_err(|_| bad())?;
        index = end;
    }
    let offset_minutes = match &text[index..] {
        "Z" | "z" => 0,
        rest if rest.len() == 6
            && (rest.starts_with('+') || rest.starts_with('-'))
            && rest.as_bytes()[3] == b':' =>
        {
            let hours = digits(index + 1..index + 3)?;
            let minutes = digits(index + 4..index + 6)?;
            if hours > 14 || minutes > 59 {
                return Err(bad());
            }
            let sign = if rest.starts_with('-') { -1 } else { 1 };
            sign * (hours * 60 + minutes)
        }
        _ => return Err(bad()),
    };
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let month_days = [
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    if !(1..=12).contains(&month)
        || day < 1
        || day > month_days[(month - 1) as usize]
        || hour > 23
        || minute > 59
        || second > 59
    {
        return Err(bad());
    }
    let days = days_from_civil(year, month, day);
    Ok(
        days * MS_PER_DAY + ((hour * 60 + minute) * 60 + second) * 1000 + millis
            - offset_minutes * 60_000,
    )
}

fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let y = year - i64::from(month <= 2);
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = month + if month > 2 { -3 } else { 9 };
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn instants_parse_with_explicit_offsets_only() {
        assert_eq!(parse_instant("0"), Ok(0));
        assert_eq!(parse_instant("1970-01-01T00:00:00Z"), Ok(0));
        assert_eq!(
            parse_instant("2026-11-03T20:00:00-05:00"),
            parse_instant("2026-11-04T01:00:00Z")
        );
        assert_eq!(parse_instant("1970-01-01T00:00:00.25Z"), Ok(250));
        assert!(parse_instant("2026-11-03T20:00:00").is_err());
        assert!(parse_instant("2026-02-30T00:00:00Z").is_err());
    }

    #[test]
    fn serials_follow_the_session_offset() {
        // 2026-11-04T04:59:59.999Z is 23:59:59.999 on Nov 3 at UTC-05:00.
        let before = parse_instant("2026-11-04T04:59:59.999Z").unwrap();
        let context = CalcContext::new(before, -300, 0);
        assert_eq!(context.today_serial(), 46_329);
        let after = CalcContext::new(before + 1, -300, 0);
        assert_eq!(after.today_serial(), 46_330);
        assert_eq!(CalcContext::new(0, 0, 0).now_serial(), 25_569.0);
        assert_eq!(CalcContext::new(43_200_000, 0, 0).now_serial(), 25_569.5);
    }

    #[test]
    fn streams_are_keyed_by_cell_site_and_draw() {
        let context = CalcContext::new(0, 0, 42);
        let a = context.uniform("Sheet1!A1", 0, 0);
        assert!((0.0..1.0).contains(&a));
        assert_eq!(a, context.uniform("Sheet1!A1", 0, 0));
        assert_ne!(a, context.uniform("Sheet1!A2", 0, 0));
        assert_ne!(a, context.uniform("Sheet1!A1", 1, 0));
        assert_ne!(a, context.uniform("Sheet1!A1", 0, 1));
    }

    #[test]
    fn context_serializes_the_seed_as_text() {
        let context = CalcContext::new(5, -300, u64::MAX);
        let text = serde_json::to_string(&context).unwrap();
        assert_eq!(
            text,
            r#"{"now_ms":5,"utc_offset_minutes":-300,"seed":"18446744073709551615"}"#
        );
        assert_eq!(serde_json::from_str::<CalcContext>(&text).unwrap(), context);
    }
}
