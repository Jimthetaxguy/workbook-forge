//! Excel defined names (`xl/workbook.xml` `definedName`), read-only.
//!
//! A name in a formula is parsed into the tree of what it refers to, so
//! dependencies and evaluation follow the target cells. Shared with Python
//! (`DefinedNames` in `python/workbook_forge/__init__.py`); the shared fixture
//! `fixtures/defined-names.v1.json` holds both to the same values and
//! messages. `docs/defined-names.md` states the rules.

use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, HashMap};
use std::rc::Rc;

use serde::{Deserialize, Serialize};

use crate::{Expr, FormulaError, MAX_FORMULA_LENGTH_UNITS, Parser, skip_quoted};

/// The most names one formula may expand, counting repeats and nesting.
pub const MAX_DEFINED_NAME_USES: usize = 256;
const BUILTIN_NAME_PREFIX: &str = "_xlnm.";

/// One defined name: `sheet` is its scope sheet, or `None` for the workbook.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DefinedName {
    pub name: String,
    #[serde(default)]
    pub sheet: Option<String>,
    pub formula: String,
    #[serde(default)]
    pub hidden: bool,
}

impl DefinedName {
    pub fn is_builtin(&self) -> bool {
        self.name.to_lowercase().starts_with(BUILTIN_NAME_PREFIX)
    }
}

/// A read-only defined-name table; lookups ignore case.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct DefinedNames {
    entries: Vec<DefinedName>,
    index: HashMap<(Option<String>, String), usize>,
}

impl DefinedNames {
    pub fn new(entries: Vec<DefinedName>) -> Self {
        let mut index = HashMap::new();
        for (position, entry) in entries.iter().enumerate() {
            let scope = entry.sheet.as_ref().map(|sheet| sheet.to_lowercase());
            let folded = entry.name.to_lowercase();
            index
                .entry((scope.clone(), folded.clone()))
                .or_insert(position);
            if let Some(short) = folded.strip_prefix(BUILTIN_NAME_PREFIX) {
                index.entry((scope, short.to_string())).or_insert(position);
            }
        }
        Self { entries, index }
    }

    /// Build the table from `definedName` attributes and text. `localSheetId`
    /// indexes the workbook's `sheets` in order; a name whose `localSheetId`
    /// names no sheet cannot be scoped and is left out.
    pub fn from_ooxml<'a>(
        nodes: impl IntoIterator<Item = (&'a BTreeMap<String, String>, &'a str)>,
        sheet_names: &[String],
    ) -> Self {
        let mut entries = Vec::new();
        for (attributes, text) in nodes {
            let Some(name) = attributes.get("name").filter(|name| !name.is_empty()) else {
                continue;
            };
            let sheet = match attributes.get("localSheetId") {
                None => None,
                Some(local) => {
                    if local.is_empty() || !local.bytes().all(|b| b.is_ascii_digit()) {
                        continue;
                    }
                    match local.parse::<usize>().ok().and_then(|i| sheet_names.get(i)) {
                        Some(sheet) => Some(sheet.clone()),
                        None => continue,
                    }
                }
            };
            let hidden = attributes
                .get("hidden")
                .is_some_and(|value| value == "1" || value == "true");
            entries.push(DefinedName {
                name: name.clone(),
                sheet,
                formula: text.to_string(),
                hidden,
            });
        }
        Self::new(entries)
    }

    pub fn entries(&self) -> &[DefinedName] {
        &self.entries
    }

    fn lookup(&self, identifier: &str, sheet: &str) -> Option<usize> {
        let folded = identifier.to_lowercase();
        self.index
            .get(&(Some(sheet.to_lowercase()), folded.clone()))
            .or_else(|| self.index.get(&(None, folded)))
            .copied()
    }

    /// The resolver a parser uses for formulas on `sheet`.
    pub(crate) fn scope(&self, sheet: &str) -> NameScope<'_> {
        NameScope {
            table: self,
            sheet: sheet.to_string(),
            state: Rc::new(NameState::default()),
        }
    }
}

#[derive(Default, Debug)]
pub(crate) struct NameState {
    stack: RefCell<Vec<usize>>,
    uses: Cell<usize>,
    /// Every identifier resolved, known or not.
    seen: Cell<usize>,
}

/// Resolves names for one formula; the cycle stack and use budget are shared
/// with the names it expands.
#[derive(Clone, Debug)]
pub(crate) struct NameScope<'n> {
    table: &'n DefinedNames,
    sheet: String,
    state: Rc<NameState>,
}

/// The diagnostic code of a defined-name message, or `None` for other text.
/// Shared with Python (`defined_name_error_code`).
pub fn defined_name_error_code(message: &str) -> Option<&'static str> {
    if message.starts_with("defined names form a cycle") {
        Some("name_cycle")
    } else if message.starts_with("defined names expand past") {
        Some("resource_limit")
    } else if message.starts_with("defined name ") {
        if message.ends_with(" could not be parsed") {
            Some("parse_error")
        } else {
            Some("unsupported_formula")
        }
    } else {
        None
    }
}

/// True when `[` appears outside a string: a link to another workbook
/// (`[1]Sheet!A1`, `'[1]My Sheet'!A1`) or a structured reference.
fn has_bracket(text: &[char]) -> bool {
    let mut index = 0;
    while index < text.len() {
        match text[index] {
            '"' => index = skip_quoted(text, index, '"'),
            '[' => return true,
            _ => index += 1,
        }
    }
    false
}

/// `Sheet!#REF!` (a name whose cells were deleted) is `#REF!`.
fn collapse_sheet_ref_errors(text: &[char]) -> String {
    let mut out = String::new();
    let mut index = 0;
    let ident = |c: char| c.is_ascii_alphanumeric() || c == '_' || c == '.';
    while index < text.len() {
        let character = text[index];
        if character == '"' {
            let end = skip_quoted(text, index, '"');
            out.extend(&text[index..end]);
            index = end;
            continue;
        }
        if character == '\'' || character.is_ascii_alphabetic() || character == '_' {
            let end = if character == '\'' {
                skip_quoted(text, index, '\'')
            } else {
                let mut end = index + 1;
                while end < text.len() && ident(text[end]) {
                    end += 1;
                }
                end
            };
            let rest: String = text[end..text.len().min(end + 6)].iter().collect();
            if rest.eq_ignore_ascii_case("!#REF!") {
                out.push_str("#REF!");
                index = end + 6;
            } else {
                out.extend(&text[index..end]);
                index = end;
            }
            continue;
        }
        out.push(character);
        index += 1;
    }
    out
}

fn absolute_cell(address: &str) -> bool {
    let Some(rest) = address.strip_prefix('$') else {
        return false;
    };
    let letters = rest.bytes().take_while(u8::is_ascii_alphabetic).count();
    if !(1..=3).contains(&letters) {
        return false;
    }
    let Some(row) = rest[letters..].strip_prefix('$') else {
        return false;
    };
    !row.is_empty() && row.bytes().all(|b| b.is_ascii_digit())
}

fn check_references(entry: &DefinedName, root: &Expr) -> Result<(), FormulaError> {
    let mut pending = vec![root];
    while let Some(expr) = pending.pop() {
        let (sheet, addresses): (&str, Vec<&str>) = match expr {
            Expr::Ref(sheet, address) => (sheet, vec![address]),
            Expr::Range(sheet, start, end) => (sheet, vec![start, end]),
            Expr::Unary(_, child) => {
                pending.push(child);
                continue;
            }
            Expr::Binary(_, left, right) => {
                pending.push(right);
                pending.push(left);
                continue;
            }
            Expr::Call(_, args) => {
                pending.extend(args.iter().rev());
                continue;
            }
            _ => continue,
        };
        if sheet.is_empty() {
            return Err(FormulaError::Unsupported(format!(
                "defined name {} refers to a cell without a sheet, which is not calculated",
                entry.name
            )));
        }
        if !addresses.iter().all(|address| absolute_cell(address)) {
            return Err(FormulaError::Unsupported(format!(
                "defined name {} uses a relative reference, which is not calculated",
                entry.name
            )));
        }
    }
    Ok(())
}

impl NameScope<'_> {
    /// How many identifiers this formula has resolved so far.
    pub(crate) fn seen(&self) -> usize {
        self.state.seen.get()
    }

    pub(crate) fn resolve(&self, identifier: &str) -> Result<Expr, FormulaError> {
        self.state.seen.set(self.state.seen.get() + 1);
        let Some(index) = self.table.lookup(identifier, &self.sheet) else {
            return Ok(Expr::Error(FormulaError::Name));
        };
        let entry = &self.table.entries[index];
        if entry.hidden || entry.is_builtin() {
            return Err(FormulaError::Unsupported(format!(
                "defined name {} is hidden or built-in and is not calculated",
                entry.name
            )));
        }
        if let Some(start) = self
            .state
            .stack
            .borrow()
            .iter()
            .position(|seen| *seen == index)
        {
            let stack = self.state.stack.borrow();
            let mut chain: Vec<&str> = stack[start..]
                .iter()
                .map(|i| self.table.entries[*i].name.as_str())
                .collect();
            chain.push(&entry.name);
            return Err(FormulaError::Unsupported(format!(
                "defined names form a cycle: {}",
                chain.join(" -> ")
            )));
        }
        self.state.uses.set(self.state.uses.get() + 1);
        if self.state.uses.get() > MAX_DEFINED_NAME_USES {
            return Err(FormulaError::Unsupported(format!(
                "defined names expand past {MAX_DEFINED_NAME_USES} uses in one formula"
            )));
        }
        let trimmed = entry.formula.trim();
        let text: Vec<char> = trimmed
            .strip_prefix('=')
            .unwrap_or(trimmed)
            .chars()
            .collect();
        if has_bracket(&text) {
            return Err(FormulaError::Unsupported(format!(
                "defined name {} refers to another workbook or a table, which is not calculated",
                entry.name
            )));
        }
        let text = collapse_sheet_ref_errors(&text);
        let unparsed = format!("defined name {} could not be parsed", entry.name);
        if text.trim().is_empty() || text.encode_utf16().count() > MAX_FORMULA_LENGTH_UNITS {
            return Err(FormulaError::Unsupported(unparsed));
        }
        let scope = NameScope {
            table: self.table,
            sheet: entry.sheet.clone().unwrap_or_else(|| self.sheet.clone()),
            state: Rc::clone(&self.state),
        };
        self.state.stack.borrow_mut().push(index);
        let parsed = Parser::with_names(&text, Some(scope)).and_then(|mut parser| {
            let expr = parser.parse_expression(0)?;
            if parser.peek().is_some() {
                return Err(FormulaError::Parse("unexpected trailing input".into()));
            }
            Ok(expr)
        });
        self.state.stack.borrow_mut().pop();
        let expr = match parsed {
            Ok(expr) => expr,
            // The messages Python also reads as unsupported (not parse
            // errors) pass through: `_is_unsupported_formula_message`.
            Err(FormulaError::Unsupported(message))
                if [
                    "unsupported name ",
                    "3D reference ",
                    "in-cell array ",
                    "defined name",
                ]
                .iter()
                .any(|prefix| message.starts_with(prefix)) =>
            {
                return Err(FormulaError::Unsupported(message));
            }
            Err(_) => return Err(FormulaError::Unsupported(unparsed)),
        };
        check_references(entry, &expr)?;
        Ok(expr)
    }
}
