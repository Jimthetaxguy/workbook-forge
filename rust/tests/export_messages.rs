//! Refusal messages never carry a machine path (AGENTS.md house rule).
//! The Python counterpart is python/tests/test_error_messages_carry_no_paths.py;
//! Rust's `io::Error` display never includes a path, so these pin the agent
//! and writer messages rather than fix them.
use serde_json::json;
use std::path::PathBuf;
use workbook_forge::agent::AgentWorkbook;
use workbook_forge::toolkit::operating_scenario;
use workbook_forge::xlsx::export_xlsx;

const TOKEN: &str = "private-home-7f3a9c";

fn private_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir()
        .join(format!("wf-{name}-{}", std::process::id()))
        .join(TOKEN);
    std::fs::remove_dir_all(&dir).ok();
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

#[test]
fn export_xlsx_refuses_an_existing_target_without_a_path() {
    let dir = private_dir("export-exists");
    let target = dir.join("out.xlsx");
    std::fs::write(&target, b"keep").unwrap();
    let refused = export_xlsx(&operating_scenario(), &target).unwrap_err();
    assert_eq!(refused.code(), "output_exists", "{refused}");
    assert_eq!(
        refused.to_string(),
        "output path already exists",
        "{refused}"
    );
    assert!(!refused.to_string().contains(TOKEN), "{refused}");
    assert_eq!(std::fs::read(&target).unwrap(), b"keep");
}

#[test]
fn agent_export_error_for_an_existing_target_carries_no_path() {
    let dir = private_dir("agent-exists");
    std::fs::write(dir.join("existing.xlsx"), b"keep").unwrap();
    let agent = AgentWorkbook::new(operating_scenario(), &dir).unwrap();
    let response = agent.handle(json!({
        "id": "test",
        "operation": "export",
        "arguments": {"filename": "existing.xlsx", "expected_revision": 0}
    }));
    assert_eq!(response["ok"], false, "{response}");
    assert_eq!(response["error"]["code"], "export_error", "{response}");
    let message = response["error"]["message"].as_str().unwrap();
    assert_eq!(message, "output path already exists", "{message}");
    assert!(!message.contains(TOKEN), "{message}");
}

#[test]
fn losing_the_link_race_is_the_shared_output_exists_refusal() {
    let dir = private_dir("link-race");
    let staged = dir.join(".staged.tmp");
    let target = dir.join("out.xlsx");
    std::fs::write(&staged, b"ours").unwrap();
    std::fs::write(&target, b"other writer").unwrap();
    let refused = workbook_forge::xlsx::link_new(&staged, &target).unwrap_err();
    assert_eq!(refused.code(), "output_exists", "{refused}");
    assert_eq!(
        refused.to_string(),
        "output path already exists",
        "{refused}"
    );
    assert!(!refused.to_string().contains(TOKEN), "{refused}");
    assert_eq!(std::fs::read(&target).unwrap(), b"other writer");
}
