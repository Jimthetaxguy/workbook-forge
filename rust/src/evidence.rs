//! Evidence grades for catalog functions, implemented independently of Python.
//!
//! Four grades exist and only the last is evidence about Excel:
//! `profile` (Workbook Forge declares the rule), `dual_engine` (Python and Rust
//! agree on shared fixtures), `expected_value` (matches a Microsoft example or
//! independent arithmetic) and `excel_observed` (Excel Desktop produced it and a
//! receipt pins the observed workbook's sha256, the Excel version and the time
//! Excel ran).
//!
//! This module validates the catalog records. Hashing receipts to detect a
//! stale observation is done by the Python harness that writes them
//! (`tools/excel_oracle.py`, `workbook_forge.evidence`), so the Rust crate does
//! not take a hashing dependency for it.

use serde_json::Value as JsonValue;
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum EvidenceGrade {
    Profile,
    DualEngine,
    ExpectedValue,
    ExcelObserved,
}

impl EvidenceGrade {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "profile" => Some(Self::Profile),
            "dual_engine" => Some(Self::DualEngine),
            "expected_value" => Some(Self::ExpectedValue),
            "excel_observed" => Some(Self::ExcelObserved),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Profile => "profile",
            Self::DualEngine => "dual_engine",
            Self::ExpectedValue => "expected_value",
            Self::ExcelObserved => "excel_observed",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProfileClass {
    Cosmetic,
    Semantic,
    Unclassified,
}

impl ProfileClass {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "cosmetic" => Some(Self::Cosmetic),
            "semantic" => Some(Self::Semantic),
            "unclassified" => Some(Self::Unclassified),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExcelObservation {
    pub receipt: String,
    pub receipt_sha256: String,
    pub workbook: String,
    pub workbook_sha256: String,
    pub excel_version: String,
    pub event_time: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FunctionEvidence {
    pub name: String,
    pub grade: EvidenceGrade,
    pub profile_class: ProfileClass,
    pub excel_observation: Option<ExcelObservation>,
}

impl FunctionEvidence {
    /// Only an `excel_observed` grade with a receipt can be Excel-compatible;
    /// whether that receipt is still current is checked where it is hashed.
    pub fn may_claim_excel_compatibility(&self) -> bool {
        self.grade == EvidenceGrade::ExcelObserved && self.excel_observation.is_some()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EvidenceError {
    pub code: &'static str,
    pub message: String,
}

impl fmt::Display for EvidenceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for EvidenceError {}

fn error(code: &'static str, message: String) -> EvidenceError {
    EvidenceError { code, message }
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn is_relative_path(value: &str) -> bool {
    !value.is_empty()
        && !value.starts_with('/')
        && !value.starts_with('~')
        && !value.contains('\\')
        && !value.split('/').any(|part| part == "..")
}

/// One parsed `HH[:MM[:SS[.fraction]]]` piece, either the clock or an offset.
struct ClockParts {
    hour: i32,
    minute: i32,
    second: i32,
    microsecond: i32,
    /// A non-digit was left over. Callers reject that at the end of the string
    /// and ignore it when a timezone marker follows the clock.
    trailing: bool,
}

fn has_zoned_time(value: &str) -> bool {
    // Same predicate as `workbook_forge.evidence._event_time`:
    // `datetime.fromisoformat` accepts the string after every uppercase `Z`
    // is rewritten to `+00:00`, and the result has a timezone. Lowercase `z`
    // is not a zone. The calendar date and clock must be in range, and the
    // offset must sit strictly inside ±24 hours. Parsing follows CPython 3.12
    // `datetime.fromisoformat`, which is what `_event_time` calls.
    !value.is_empty() && fromisoformat_has_timezone(&value.replace('Z', "+00:00"))
}

fn fromisoformat_has_timezone(value: &str) -> bool {
    // `datetime.fromisoformat` rejects anything shorter than 7 characters.
    if value.chars().count() < 7 {
        return false;
    }
    let bytes = value.as_bytes();
    let Some(separator) = find_datetime_separator(bytes) else {
        return false;
    };
    if separator > bytes.len() {
        return false;
    }
    let Some((year, month, day)) = parse_iso_date(bytes, separator) else {
        return false;
    };
    let (hour, minute, second, microsecond, has_zone) = if bytes.len() > separator {
        let time_at = separator + utf8_char_width(bytes[separator]);
        if time_at > bytes.len() {
            return false;
        }
        let Some(parsed) = parse_iso_time(bytes, time_at) else {
            return false;
        };
        parsed
    } else {
        (0, 0, 0, 0, false)
    };
    valid_calendar_date(year, month, day)
        && valid_clock(hour, minute, second, microsecond)
        && has_zone
}

fn utf8_char_width(lead: u8) -> usize {
    // Width of the one-character date/time separator. Any scalar is allowed
    // there, including a lowercase `t`.
    if lead & 0x80 == 0 {
        1
    } else if lead & 0xf0 == 0xe0 {
        3
    } else if lead & 0xf0 == 0xf0 {
        4
    } else {
        2
    }
}

fn find_datetime_separator(bytes: &[u8]) -> Option<usize> {
    // Byte index of the single separator between the date and the time.
    // A return of `bytes.len()` means the string is a date with no time.
    // Mirrors `_find_isoformat_datetime_separator` in CPython 3.12.
    let len = bytes.len();
    if len == 7 {
        return Some(7);
    }
    if bytes.get(4) == Some(&b'-') {
        if bytes.get(5) == Some(&b'W') {
            if len < 8 {
                return None;
            }
            if len > 8 && bytes.get(8) == Some(&b'-') {
                if len == 9 {
                    return None;
                }
                if len > 10 && bytes.get(10).is_some_and(u8::is_ascii_digit) {
                    // `YYYY-Www-##`: the hyphen is the separator, not a week day.
                    return Some(8);
                }
                return Some(10);
            }
            return Some(8);
        }
        return Some(10);
    }
    if bytes.get(4) == Some(&b'W') {
        let mut index = 7;
        while index < len && bytes[index].is_ascii_digit() {
            index += 1;
        }
        if index < 9 {
            return Some(index);
        }
        // An even index is `YYYYWww`; an odd index is `YYYYWwwd`.
        return Some(if index % 2 == 0 { 7 } else { 8 });
    }
    Some(8)
}

fn parse_iso_date(bytes: &[u8], len: usize) -> Option<(i32, i32, i32)> {
    let mut pos = 0;
    let year = read_digits(bytes, &mut pos, 4)?;
    let separated = bytes.get(pos) == Some(&b'-');
    if separated {
        pos += 1;
    }
    if bytes.get(pos) == Some(&b'W') {
        pos += 1;
        let week = read_digits(bytes, &mut pos, 2)?;
        let day = if pos < len {
            if separated {
                if bytes.get(pos) != Some(&b'-') {
                    return None;
                }
                pos += 1;
            }
            read_digits(bytes, &mut pos, 1)?
        } else {
            1
        };
        return iso_week_to_gregorian(year, week, day);
    }
    let month = read_digits(bytes, &mut pos, 2)?;
    if separated {
        if bytes.get(pos) != Some(&b'-') {
            return None;
        }
        pos += 1;
    }
    let day = read_digits(bytes, &mut pos, 2)?;
    Some((year, month, day))
}

fn parse_iso_time(bytes: &[u8], start: usize) -> Option<(i32, i32, i32, i32, bool)> {
    let mut zone_at = start;
    while zone_at < bytes.len() {
        let byte = bytes[zone_at];
        if byte == b'Z' || byte == b'+' || byte == b'-' {
            break;
        }
        zone_at += 1;
    }
    let clock = parse_hh_mm_ss_ff(bytes, start, zone_at)?;
    if zone_at == bytes.len() {
        if clock.trailing {
            return None;
        }
        return Some((
            clock.hour,
            clock.minute,
            clock.second,
            clock.microsecond,
            false,
        ));
    }
    if bytes[zone_at] == b'Z' {
        if zone_at + 1 != bytes.len() {
            return None;
        }
        return Some((
            clock.hour,
            clock.minute,
            clock.second,
            clock.microsecond,
            true,
        ));
    }
    let sign = if bytes[zone_at] == b'-' { -1 } else { 1 };
    let offset = parse_hh_mm_ss_ff(bytes, zone_at + 1, bytes.len())?;
    if offset.trailing || !offset_inside_one_day(sign, &offset) {
        return None;
    }
    Some((
        clock.hour,
        clock.minute,
        clock.second,
        clock.microsecond,
        true,
    ))
}

fn parse_hh_mm_ss_ff(bytes: &[u8], start: usize, end: usize) -> Option<ClockParts> {
    // `end` is the first byte of a `+`/`-`/`Z` marker, or `bytes.len()`.
    // The byte at `end` (or a virtual NUL past the string) terminates a field.
    let mut fields = [0i32; 3];
    let mut pos = start;
    let mut separated = true;
    let mut index = 0;
    while index < 3 {
        fields[index] = read_digits(bytes, &mut pos, 2)?;
        let marker = byte_or_nul(bytes, pos);
        pos += 1;
        if index == 0 {
            separated = marker == b':';
        }
        if pos >= end {
            return Some(ClockParts {
                hour: fields[0],
                minute: fields[1],
                second: fields[2],
                microsecond: 0,
                trailing: marker != 0,
            });
        }
        if separated && marker == b':' {
            index += 1;
            continue;
        }
        if marker == b'.' || marker == b',' {
            break;
        }
        if !separated {
            pos -= 1;
            index += 1;
            continue;
        }
        return None;
    }
    let remains = end.saturating_sub(pos);
    let digits = remains.min(6);
    if digits == 0 {
        return None;
    }
    let mut microsecond = read_digits(bytes, &mut pos, digits)?;
    if digits < 6 {
        // Left-pad a short fraction out to microseconds. Extra digits past 6
        // are truncated, not rounded.
        const SCALE: [i32; 5] = [100_000, 10_000, 1_000, 100, 10];
        microsecond *= SCALE[digits - 1];
    }
    while bytes.get(pos).is_some_and(u8::is_ascii_digit) {
        pos += 1;
    }
    Some(ClockParts {
        hour: fields[0],
        minute: fields[1],
        second: fields[2],
        microsecond,
        trailing: byte_or_nul(bytes, pos) != 0,
    })
}

fn read_digits(bytes: &[u8], pos: &mut usize, count: usize) -> Option<i32> {
    let mut value = 0i32;
    for _ in 0..count {
        let byte = *bytes.get(*pos)?;
        if !byte.is_ascii_digit() {
            return None;
        }
        value = value * 10 + i32::from(byte - b'0');
        *pos += 1;
    }
    Some(value)
}

fn byte_or_nul(bytes: &[u8], index: usize) -> u8 {
    bytes.get(index).copied().unwrap_or(0)
}

fn offset_inside_one_day(sign: i32, offset: &ClockParts) -> bool {
    // Python accepts `+00:60` (one normalized hour) and rejects `+24:00`.
    // The components themselves are not limited to a clock; the total must be
    // strictly inside ±24 hours, which is `datetime.timezone`'s range.
    let total = i64::from(sign)
        * (i64::from(offset.hour) * 3_600_000_000
            + i64::from(offset.minute) * 60_000_000
            + i64::from(offset.second) * 1_000_000
            + i64::from(offset.microsecond));
    let limit = 24i64 * 3_600_000_000;
    // Exclusive on both sides: `+24:00` and `-24:00` are out of range.
    total > -limit && total < limit
}

fn valid_clock(hour: i32, minute: i32, second: i32, microsecond: i32) -> bool {
    (0..=23).contains(&hour)
        && (0..=59).contains(&minute)
        && (0..=59).contains(&second)
        && (0..=999_999).contains(&microsecond)
}

const DAYS_IN_MONTH: [i32; 13] = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
const DAYS_BEFORE_MONTH: [i32; 13] = [0, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];

fn is_leap_year(year: i32) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

fn days_in_month(year: i32, month: i32) -> i32 {
    if month == 2 && is_leap_year(year) {
        29
    } else {
        DAYS_IN_MONTH[month as usize]
    }
}

fn valid_calendar_date(year: i32, month: i32, day: i32) -> bool {
    (1..=9999).contains(&year)
        && (1..=12).contains(&month)
        && (1..=days_in_month(year, month)).contains(&day)
}

fn days_before_year(year: i32) -> i32 {
    let year = year - 1;
    year * 365 + year / 4 - year / 100 + year / 400
}

fn days_before_month(year: i32, month: i32) -> i32 {
    let mut days = DAYS_BEFORE_MONTH[month as usize];
    if month > 2 && is_leap_year(year) {
        days += 1;
    }
    days
}

fn ymd_to_ordinal(year: i32, month: i32, day: i32) -> i32 {
    days_before_year(year) + days_before_month(year, month) + day
}

fn ordinal_to_ymd(mut ordinal: i32) -> Option<(i32, i32, i32)> {
    // 1 January 0001 is ordinal 1. Mirrors CPython `ord_to_ymd`.
    if ordinal < 1 {
        return None;
    }
    ordinal -= 1;
    const DAYS_IN_400_YEARS: i32 = 146_097;
    const DAYS_IN_100_YEARS: i32 = 36_524;
    const DAYS_IN_4_YEARS: i32 = 1_461;
    let cycles_400 = ordinal / DAYS_IN_400_YEARS;
    let mut remainder = ordinal % DAYS_IN_400_YEARS;
    let mut year = cycles_400 * 400 + 1;
    let cycles_100 = remainder / DAYS_IN_100_YEARS;
    remainder %= DAYS_IN_100_YEARS;
    let cycles_4 = remainder / DAYS_IN_4_YEARS;
    remainder %= DAYS_IN_4_YEARS;
    let years = remainder / 365;
    remainder %= 365;
    year += cycles_100 * 100 + cycles_4 * 4 + years;
    if years == 4 || cycles_100 == 4 {
        return Some((year - 1, 12, 31));
    }
    let leap = years == 3 && (cycles_4 != 24 || cycles_100 == 3);
    let mut month = (remainder + 50) >> 5;
    if !(1..=12).contains(&month) {
        return None;
    }
    let mut preceding = DAYS_BEFORE_MONTH[month as usize];
    if month > 2 && leap {
        preceding += 1;
    }
    if preceding > remainder {
        month -= 1;
        if !(1..=12).contains(&month) {
            return None;
        }
        preceding -= days_in_month(year, month);
    }
    remainder -= preceding;
    Some((year, month, remainder + 1))
}

fn weekday_monday_zero(year: i32, month: i32, day: i32) -> i32 {
    (ymd_to_ordinal(year, month, day) + 6) % 7
}

fn iso_week1_monday(year: i32) -> i32 {
    let first = ymd_to_ordinal(year, 1, 1);
    let first_weekday = (first + 6) % 7;
    let mut monday = first - first_weekday;
    if first_weekday > 3 {
        monday += 7;
    }
    monday
}

fn iso_week_to_gregorian(year: i32, week: i32, day: i32) -> Option<(i32, i32, i32)> {
    let week_in_year = if (1..53).contains(&week) {
        true
    } else if week == 53 {
        // A year has 53 ISO weeks when 1 January is a Thursday, or a Wednesday
        // in a leap year. Monday is 0.
        let first = weekday_monday_zero(year, 1, 1);
        first == 3 || (first == 2 && is_leap_year(year))
    } else {
        false
    };
    if !week_in_year || !(1..=7).contains(&day) {
        return None;
    }
    let ordinal = iso_week1_monday(year) + (week - 1) * 7 + day - 1;
    ordinal_to_ymd(ordinal)
}

const OBSERVATION_FIELDS: [&str; 6] = [
    "receipt",
    "receipt_sha256",
    "workbook",
    "workbook_sha256",
    "excel_version",
    "event_time",
];

fn observation(record: &JsonValue, name: &str) -> Result<ExcelObservation, EvidenceError> {
    let object = record.as_object().ok_or_else(|| {
        error(
            "invalid_observation",
            format!("function {name} excel_observation must be an object"),
        )
    })?;
    if let Some(unknown) = object
        .keys()
        .find(|key| !OBSERVATION_FIELDS.contains(&key.as_str()))
    {
        return Err(error(
            "invalid_observation",
            format!("function {name} excel_observation has unknown field {unknown}"),
        ));
    }
    let field = |key: &str| -> Result<String, EvidenceError> {
        object
            .get(key)
            .and_then(JsonValue::as_str)
            .map(str::to_owned)
            .ok_or_else(|| {
                error(
                    "invalid_observation",
                    format!("function {name} excel_observation is missing {key}"),
                )
            })
    };
    let parsed = ExcelObservation {
        receipt: field("receipt")?,
        receipt_sha256: field("receipt_sha256")?,
        workbook: field("workbook")?,
        workbook_sha256: field("workbook_sha256")?,
        excel_version: field("excel_version")?,
        event_time: field("event_time")?,
    };
    if !is_sha256(&parsed.receipt_sha256) || !is_sha256(&parsed.workbook_sha256) {
        return Err(error(
            "invalid_observation",
            format!("function {name} excel_observation hashes must be lowercase sha256"),
        ));
    }
    if !is_relative_path(&parsed.receipt) || !is_relative_path(&parsed.workbook) {
        return Err(error(
            "invalid_observation",
            format!("function {name} excel_observation paths must be repository-relative"),
        ));
    }
    if parsed.excel_version.trim().is_empty() || !has_zoned_time(&parsed.event_time) {
        return Err(error(
            "invalid_observation",
            format!(
                "function {name} excel_observation needs an Excel version and a zoned event_time"
            ),
        ));
    }
    Ok(parsed)
}

/// Validate one `catalog/formulas.json` function entry's evidence fields.
pub fn validate_function_evidence(entry: &JsonValue) -> Result<FunctionEvidence, EvidenceError> {
    let name = entry["name"].as_str().unwrap_or("?").to_ascii_uppercase();
    let grade = entry["evidence_grade"]
        .as_str()
        .and_then(EvidenceGrade::parse)
        .ok_or_else(|| {
            error(
                "invalid_evidence_grade",
                format!("function {name} has no valid evidence_grade"),
            )
        })?;
    let profile_class = match entry.get("profile_class") {
        None => ProfileClass::Unclassified,
        Some(value) => value
            .as_str()
            .and_then(ProfileClass::parse)
            .ok_or_else(|| {
                error(
                    "invalid_profile_class",
                    format!("function {name} has an invalid profile_class"),
                )
            })?,
    };
    let excel_observation = match (grade, entry.get("excel_observation")) {
        (EvidenceGrade::ExcelObserved, Some(record)) => Some(observation(record, &name)?),
        (EvidenceGrade::ExcelObserved, None) => {
            return Err(error(
                "missing_receipt",
                format!("function {name} is excel_observed without a receipt"),
            ));
        }
        (_, Some(_)) => {
            return Err(error(
                "grade_mismatch",
                format!(
                    "function {name} carries an Excel observation but is graded {}",
                    grade.as_str()
                ),
            ));
        }
        (_, None) => None,
    };
    let evidence = FunctionEvidence {
        name: name.clone(),
        grade,
        profile_class,
        excel_observation,
    };
    match entry.get("excel_compatible") {
        None | Some(JsonValue::Bool(false)) => {}
        Some(JsonValue::Bool(true)) if evidence.may_claim_excel_compatibility() => {}
        Some(JsonValue::Bool(true)) => {
            return Err(error(
                "excel_compatibility_without_observation",
                format!(
                    "function {name} is graded {}; only excel_observed with a receipt can be Excel-compatible",
                    grade.as_str()
                ),
            ));
        }
        Some(_) => {
            return Err(error(
                "invalid_excel_compatible",
                format!("function {name} excel_compatible must be boolean"),
            ));
        }
    }
    Ok(evidence)
}

/// Validate every function entry in a parsed `formulas.json` document.
pub fn validate_catalog_evidence(
    catalog: &JsonValue,
) -> Result<Vec<FunctionEvidence>, EvidenceError> {
    catalog["functions"]
        .as_array()
        .ok_or_else(|| {
            error(
                "invalid_catalog",
                "formulas.json must list functions".into(),
            )
        })?
        .iter()
        .map(validate_function_evidence)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn catalog() -> JsonValue {
        serde_json::from_str(
            &std::fs::read_to_string(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/../catalog/formulas.json"
            ))
            .unwrap(),
        )
        .unwrap()
    }

    fn observation_record() -> JsonValue {
        json!({
            "receipt": "receipts/sum-excel/receipt.json",
            "receipt_sha256": "a".repeat(64),
            "workbook": "receipts/sum-excel/roundtrip.xlsx",
            "workbook_sha256": "b".repeat(64),
            "excel_version": "16.113.4",
            "event_time": "2026-10-14T18:00:03Z",
        })
    }

    #[test]
    fn checked_in_catalog_grades_every_function_and_claims_no_excel_observation() {
        let graded = validate_catalog_evidence(&catalog()).unwrap();
        assert!(!graded.is_empty());
        assert!(
            graded
                .iter()
                .all(|item| item.grade != EvidenceGrade::ExcelObserved)
        );
        assert!(
            graded
                .iter()
                .all(|item| item.profile_class == ProfileClass::Unclassified)
        );
        assert!(
            graded
                .iter()
                .all(|item| !item.may_claim_excel_compatibility())
        );
    }

    #[test]
    fn dual_engine_only_cannot_be_marked_excel_compatible() {
        for grade in ["profile", "dual_engine", "expected_value"] {
            let entry = json!({"name": "SUM", "evidence_grade": grade, "excel_compatible": true});
            assert_eq!(
                validate_function_evidence(&entry).unwrap_err().code,
                "excel_compatibility_without_observation"
            );
        }
        let entry = json!({
            "name": "SUM", "evidence_grade": "dual_engine",
            "excel_observation": observation_record(),
        });
        assert_eq!(
            validate_function_evidence(&entry).unwrap_err().code,
            "grade_mismatch"
        );
    }

    #[test]
    fn excel_observed_requires_a_well_formed_receipt() {
        let missing = json!({"name": "SUM", "evidence_grade": "excel_observed"});
        assert_eq!(
            validate_function_evidence(&missing).unwrap_err().code,
            "missing_receipt"
        );
        for (field, bad) in [
            ("receipt_sha256", "short"),
            ("workbook", "/srv/forge/roundtrip.xlsx"),
            ("receipt", "../receipt.json"),
            ("event_time", "2026-10-14 18:00"),
            ("excel_version", " "),
        ] {
            let mut record = observation_record();
            record[field] = json!(bad);
            let entry = json!({
                "name": "SUM", "evidence_grade": "excel_observed", "excel_observation": record,
            });
            assert_eq!(
                validate_function_evidence(&entry).unwrap_err().code,
                "invalid_observation",
                "{field}"
            );
        }
        let entry = json!({
            "name": "SUM", "evidence_grade": "excel_observed", "profile_class": "semantic",
            "excel_observation": observation_record(), "excel_compatible": true,
        });
        let evidence = validate_function_evidence(&entry).unwrap();
        assert!(evidence.may_claim_excel_compatibility());
        assert_eq!(evidence.profile_class, ProfileClass::Semantic);
    }

    #[test]
    fn profile_class_defaults_to_unclassified() {
        let entry = json!({"name": "SUM", "evidence_grade": "dual_engine"});
        assert_eq!(
            validate_function_evidence(&entry).unwrap().profile_class,
            ProfileClass::Unclassified
        );
        let entry =
            json!({"name": "SUM", "evidence_grade": "dual_engine", "profile_class": "maybe"});
        assert_eq!(
            validate_function_evidence(&entry).unwrap_err().code,
            "invalid_profile_class"
        );
    }

    /// Strings `workbook_forge.evidence._event_time` accepts or rejects.
    /// The Python column is what `datetime.fromisoformat` decides after the
    /// uppercase-`Z` rewrite; the Rust check has to agree on every row.
    fn zoned_event_time_cases() -> &'static [(&'static str, bool)] {
        &[
            ("2026-99-99T99:99:99Z", false),
            ("2026-10-14t18:00:03Z", true),
            ("2026-10-14T18:00:03Z", true),
            ("2026-10-14T18:00:03z", false),
            ("2026-10-14T18:00:03", false),
            ("2026-10-14", false),
            ("2026-10-14T18:00:03+00:00", true),
            ("2026-10-14T18:00:03-00:00", true),
            ("2026-10-14T18:00:03-05:00", true),
            ("2026-10-14T18:00:03+0530", true),
            ("2026-10-14T18:00:03-0530", true),
            ("2026-10-14T18:00:03+05", true),
            ("2026-10-14T18:00:03-05", true),
            ("2026-10-14T18:00:03+05:30:00", true),
            ("2026-10-14T18:00:03+053000", true),
            ("2026-10-14T18:00:03+00:60", true),
            ("2026-10-14T18:00:03+23:59", true),
            ("2026-10-14T18:00:03-23:59", true),
            ("2026-10-14T18:00:03+23:59:59.999999", true),
            ("2026-10-14T18:00:03-23:59:59.999999", true),
            ("2026-10-14T18:00:03+24:00", false),
            ("2026-10-14T18:00:03-24:00", false),
            ("2026-10-14T18:00:03+99", false),
            ("2026-10-14T18:00:03+5:00", false),
            ("2026-10-14T18:00:03+1", false),
            ("2026-10-14T18:00:03+", false),
            ("2026-10-14T18:00:03+00:0", false),
            ("2026-10-14T18:00:03+000", false),
            ("2026-10-14T18:00:03UTC", false),
            ("2026-10-14T18:00:03+00:00Z", false),
            ("2026-02-29T00:00:00Z", false),
            ("2024-02-29T12:00:00Z", true),
            ("1900-02-29T00:00:00Z", false),
            ("2000-02-29T00:00:00Z", true),
            ("2100-02-29T00:00:00Z", false),
            ("2400-02-29T00:00:00Z", true),
            ("2026-04-31T00:00:00Z", false),
            ("2026-11-31T00:00:00Z", false),
            ("2026-10-14T24:00:00Z", false),
            ("2026-10-14T23:60:00Z", false),
            ("2026-10-14T23:59:60Z", false),
            ("0001-01-01T00:00:00Z", true),
            ("9999-12-31T23:59:59.999999Z", true),
            ("0000-01-01T00:00:00Z", false),
            ("2026-10-14 18:00:03Z", true),
            ("2026-10-14T18:00:03.123456Z", true),
            ("2026-10-14T18:00:03,123456+00:00", true),
            ("2026-10-14T18Z", true),
            ("2026-10-14T18:00Z", true),
            ("20261014T180003Z", true),
            ("2026-10-14T180003Z", true),
            ("2026-W41-3T18:00:03Z", true),
            ("2026-w41-3T18:00:03Z", false),
            ("2020-W53-4T00:00:00Z", true),
            ("2025-W53-1T00:00:00Z", false),
            ("9999-W52-5T00:00:00Z", true),
            ("9999-W52-7T00:00:00Z", false),
            ("2026-10-14t18:00:03-04:00", true),
            ("2026-10-14T18:00:03 +00:00", true),
            ("2026-10-14T18:00:03  +00:00", false),
        ]
    }

    fn python_event_times(values: &[&str]) -> Vec<bool> {
        // The same predicate as `workbook_forge.evidence._event_time`.
        let script = r#"
import json, sys
from datetime import datetime

def accept(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None

json.dump([accept(item) for item in json.load(sys.stdin)], sys.stdout)
"#;
        let mut child = std::process::Command::new("python3")
            .arg("-c")
            .arg(script)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .expect("python3 is required to compare event_time with datetime.fromisoformat");
        {
            use std::io::Write;
            let mut stdin = child.stdin.take().expect("python stdin");
            stdin
                .write_all(serde_json::to_string(values).unwrap().as_bytes())
                .unwrap();
        }
        let output = child.wait_with_output().unwrap();
        assert!(
            output.status.success(),
            "python event_time check failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        serde_json::from_slice(&output.stdout).unwrap()
    }

    #[test]
    fn zoned_event_time_matches_python_fromisoformat() {
        let cases = zoned_event_time_cases();
        let values: Vec<&str> = cases.iter().map(|(value, _)| *value).collect();
        let python = python_event_times(&values);
        for ((value, expected), python_accepts) in cases.iter().zip(python) {
            assert_eq!(python_accepts, *expected, "python {value}");
            assert_eq!(has_zoned_time(value), python_accepts, "rust {value}");
        }
    }

    #[test]
    fn impossible_calendar_time_cannot_claim_excel_compatibility() {
        let mut record = observation_record();
        record["event_time"] = json!("2026-99-99T99:99:99Z");
        let entry = json!({
            "name": "SUM",
            "evidence_grade": "excel_observed",
            "excel_observation": record,
            "excel_compatible": true,
        });
        assert_eq!(
            validate_function_evidence(&entry).unwrap_err().code,
            "invalid_observation"
        );

        let mut record = observation_record();
        record["event_time"] = json!("2026-10-14t18:00:03Z");
        let entry = json!({
            "name": "SUM",
            "evidence_grade": "excel_observed",
            "excel_observation": record,
            "excel_compatible": true,
        });
        let evidence = validate_function_evidence(&entry).unwrap();
        assert!(evidence.may_claim_excel_compatibility());
        assert_eq!(
            evidence.excel_observation.unwrap().event_time,
            "2026-10-14t18:00:03Z"
        );
    }
}
