"""Regression tests for the conftest live-system guard's argv handling.

The guard must treat only argv[0] of a list/tuple command as the executable
(arguments are data: a file named ``skill`` is not the ``skill`` binary),
while still scanning every token of wrapper invocations like ``bash -c``.
All blocked-case commands use patterns that match no real process, so a
guard regression cannot kill anything.
"""

import subprocess

import pytest


def test_argv_arguments_are_not_treated_as_executables(tmp_path):
    """A file argument whose basename is a killer name must not trip the
    guard (the path contains "hermes" via the pytest tmp root)."""
    target = tmp_path / "skill"
    target.write_text("just a filename\n")
    result = subprocess.run(["cat", str(target)], capture_output=True, text=True)
    assert result.returncode == 0
    assert "just a filename" in result.stdout


def test_direct_killer_argv_is_still_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["pkill", "-f", "hermes-guard-regression-nomatch"])


def test_wrapped_killer_command_is_still_blocked():
    """argv[0]-only scanning must not exempt commands hidden behind a
    shell wrapper."""
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["bash", "-c", "pkill -f hermes-guard-regression-nomatch"])


def test_env_wrapped_killer_command_is_still_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["env", "GUARD_TEST=1", "pkill", "-f", "hermes-guard-regression-nomatch"])


def test_gateway_start_inside_a_container_exec_is_not_blocked():
    """``docker exec <ctr> hermes gateway start`` launches the gateway INSIDE the container,
    where it cannot reach the host's systemd unit or webhook port; tests/docker/ depends on it.
    The binary is a stub so the argv stays exact without needing a Docker daemon."""
    import os
    import stat

    stub_dir = os.path.join(os.environ["HERMES_HOME"], "stub-bin")
    os.makedirs(stub_dir, exist_ok=True)
    stub = os.path.join(stub_dir, "docker")
    with open(stub, "w") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)
    result = subprocess.run(
        [stub, "exec", "-u", "hermes", "ctr", "sh", "-c", "hermes -p prof gateway start"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_gateway_start_on_the_host_is_still_blocked():
    with pytest.raises(RuntimeError, match="REAL.*gateway runtime"):
        subprocess.run(["python", "-m", "hermes_cli.main", "gateway", "start"])


@pytest.mark.parametrize("verb", ["kickstart", "bootout", "bootstrap"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_launchd_mutations_are_blocked_without_executing_a_real_service(tmp_path, verb, wrapped):
    # Even a broken guard can only invoke this harmless temporary command.
    command = tmp_path / "launchctl"
    command.write_text("#!/bin/sh\nexit 0\n")
    command.chmod(0o755)
    argv = [str(command), verb, "gui/501/ai.hermes.gateway.guard-regression-nomatch"]
    if wrapped:
        import shlex
        argv = ["sh", "-c", shlex.join(argv)]
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(argv, check=True)


def test_launchd_read_only_probe_is_allowed(tmp_path):
    command = tmp_path / "launchctl"
    command.write_text("#!/bin/sh\nprintf 'listed'\n")
    command.chmod(0o755)
    result = subprocess.run([str(command), "list", "ai.hermes.gateway.guard-regression-nomatch"],
                            capture_output=True, text=True, check=True)
    assert result.stdout == "listed"
