"""Tests for sprint shortcut prompt builders."""

from hermes_cli.commands import COMMANDS, GATEWAY_KNOWN_COMMANDS, resolve_command
from hermes_cli.sprint_commands import build_sprint_shortcut_prompt


def test_sprint_shortcuts_are_registered_for_cli_and_gateway():
    for name in ("plan_sprint", "run_sprint", "continue_sprint", "auto_agent"):
        cmd = resolve_command(name)
        assert cmd is not None
        assert cmd.name == name
        assert f"/{name}" in COMMANDS
        assert name in GATEWAY_KNOWN_COMMANDS


def test_plan_sprint_requires_goal():
    shortcut = build_sprint_shortcut_prompt("plan_sprint", "")
    assert shortcut.prompt == ""
    assert shortcut.usage == "Usage: /plan_sprint <goal>"


def test_plan_sprint_prompt_creates_plan_only():
    shortcut = build_sprint_shortcut_prompt("plan_sprint", "Improve mobile dashboard")
    assert shortcut.usage is None
    assert "create a sprint-gated plan" in shortcut.prompt
    assert "Improve mobile dashboard" in shortcut.prompt
    assert "Do not edit product code yet" in shortcut.prompt
    assert "sprint-plan-executor" in shortcut.prompt


def test_run_sprint_auto_prompt_discovers_plan_and_worker():
    shortcut = build_sprint_shortcut_prompt("run_sprint", "auto")
    assert shortcut.usage is None
    assert "latest active unfinished plan" in shortcut.prompt
    assert "auto-select worker profile" in shortcut.prompt or "worker/profile" in shortcut.prompt
    assert "Execute exactly one sprint" in shortcut.prompt
    assert "Update the plan file" in shortcut.prompt


def test_continue_sprint_defaults_to_auto():
    shortcut = build_sprint_shortcut_prompt("continue_sprint", None)
    assert shortcut.usage is None
    assert "Mode/args: auto" in shortcut.prompt
    assert "continue the latest active sprint plan" in shortcut.prompt


def test_auto_agent_requires_task_and_routes_profile():
    missing = build_sprint_shortcut_prompt("auto_agent", "")
    assert missing.usage == "Usage: /auto_agent <task>"

    shortcut = build_sprint_shortcut_prompt("auto_agent", "Fix the login bug")
    assert shortcut.usage is None
    assert "Fix the login bug" in shortcut.prompt
    assert "coordinator-selected worker/profile" in shortcut.prompt
    assert "Verify results" in shortcut.prompt
