//! Bounded native primitive conformance requests on stdin, one response per line.
fn main() -> std::io::Result<()> {
    workbook_forge::primitives::serve_contract(std::io::stdin().lock(), std::io::stdout().lock())
}
