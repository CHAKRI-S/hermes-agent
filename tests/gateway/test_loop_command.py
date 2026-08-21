"""Gateway /loop command tests — dispatch, routing capture, mid-run guard."""

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from hermes_cli import goals, loops


class _FakeSessionEntry:
    session_id = "sid-gateway-loop"


class _FakeSessionStore:
    def __init__(self):
        self.entry = _FakeSessionEntry()

    def get_or_create_session(self, source, *, touch_activity=True):
        return self.entry

    def _generate_session_key(self, source):
        return "agent:main:discord:channel:loop-test"


@pytest.fixture
def loop_env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    goals._DB_CACHE.clear()
    yield home
    goals._DB_CACHE.clear()


def _make_runner():
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.DISCORD: PlatformConfig(enabled=True, token="token")}
    )
    runner.session_store = _FakeSessionStore()
    runner.adapters = {}
    runner._queued_events = {}
    return runner


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.DISCORD,
            chat_id="chat-loop",
            chat_type="channel",
            thread_id="thread-9",
            user_id="user-loop",
        ),
        message_id="msg-loop",
    )


@pytest.mark.asyncio
async def test_gateway_loop_create_captures_route(loop_env):
    runner = _make_runner()
    response = await GatewayRunner._handle_loop_command(runner, _make_event("/loop 5m check the deploy"))
    assert "Loop set" in response
    assert "every 5m" in response

    state = loops.load_loop("sid-gateway-loop")
    assert state is not None
    assert state.prompt == "check the deploy"
    assert state.route["platform"] == "discord"
    assert state.route["chat_id"] == "chat-loop"
    assert state.route["thread_id"] == "thread-9"
    assert state.route["profile"] == "default"


@pytest.mark.asyncio
async def test_gateway_loop_create_captures_named_profile(loop_env):
    runner = _make_runner()
    event = _make_event("/loop 5m check the deploy")
    event.source.profile = "worker"

    await GatewayRunner._handle_loop_command(runner, event)

    state = loops.load_loop("sid-gateway-loop")
    assert state is not None
    assert state.route["profile"] == "worker"


@pytest.mark.asyncio
async def test_legacy_loop_route_recovers_profile_from_session_record(loop_env):
    """Routes persisted before profile capture recover their owning namespace."""
    runner = _make_runner()
    runner.config.multiplex_profiles = True
    runner._session_db = SimpleNamespace(
        get_session=AsyncMock(return_value={"profile_name": "worker"})
    )

    profile, proven = await GatewayRunner._resolve_loop_route_profile(
        runner, "sid-legacy-worker", {}
    )

    assert (profile, proven) == ("worker", True)


@pytest.mark.asyncio
async def test_legacy_loop_route_without_owner_fails_closed(loop_env):
    """Ambiguous multiplexed legacy routes must never borrow the default bot."""
    runner = _make_runner()
    runner.config.multiplex_profiles = True
    runner._session_db = SimpleNamespace(get_session=AsyncMock(return_value=None))

    profile, proven = await GatewayRunner._resolve_loop_route_profile(
        runner, "sid-legacy-unknown", {}
    )

    assert (profile, proven) == (None, False)


@pytest.mark.asyncio
async def test_loop_wakeup_uses_named_profile_adapter(loop_env, monkeypatch):
    """Legacy persisted wakeups recover and use the profile that owns them."""
    runner = _make_runner()
    runner.config.multiplex_profiles = True
    default_adapter = SimpleNamespace(handle_message=AsyncMock())
    worker_adapter = SimpleNamespace(handle_message=AsyncMock())
    runner.adapters = {Platform.DISCORD: default_adapter}
    runner._profile_adapters = {"worker": {Platform.DISCORD: worker_adapter}}
    runner._running = True
    runner._running_agents = {}
    runner._session_source_cache = {}
    runner.session_store = SimpleNamespace(_ensure_loaded=lambda: None, _entries={})
    runner._session_db = SimpleNamespace(
        get_session=AsyncMock(return_value={"profile_name": "worker"})
    )
    runner._warm_goals_session_db = AsyncMock()
    runner._session_key_for_source = lambda _source: (
        "agent:worker:discord:channel:chat-loop:thread-9"
    )

    route = {
        "platform": "discord",
        "chat_id": "chat-loop",
        "chat_type": "channel",
        "thread_id": "thread-9",
        "user_id": "user-loop",
    }
    loop_state = SimpleNamespace(
        route=route,
        awaiting_response=False,
        next_due_at=0,
    )

    class _FakeLoopManager:
        def __init__(self, session_id):
            self.session_id = session_id
            self.state = SimpleNamespace(ticks_fired=1)

        def is_due(self, _now):
            return True

        def fire_tick(self):
            return "continue the loop"

        def abandon_tick(self):
            raise AssertionError("named-profile wakeup should be deliverable")

    monkeypatch.setattr(loops, "list_active_loops", lambda: [("sid-loop", loop_state)])
    monkeypatch.setattr(loops, "LoopManager", _FakeLoopManager)
    monkeypatch.setattr(loops, "goal_blocks_loop_tick", lambda _sid: False)

    sleep_calls = 0

    async def _bounded_sleep(_delay):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            runner._running = False

    monkeypatch.setattr(asyncio, "sleep", _bounded_sleep)

    await GatewayRunner._loop_wakeup_watcher(runner, interval=0)

    worker_adapter.handle_message.assert_awaited_once()
    default_adapter.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_loop_status_pause_stop(loop_env):
    runner = _make_runner()
    await GatewayRunner._handle_loop_command(runner, _make_event("/loop 5m poll CI"))

    status = await GatewayRunner._handle_loop_command(runner, _make_event("/loop status"))
    assert "poll CI" in status

    paused = await GatewayRunner._handle_loop_command(runner, _make_event("/loop pause"))
    assert "paused" in paused.lower()

    stopped = await GatewayRunner._handle_loop_command(runner, _make_event("/loop stop"))
    assert "stopped" in stopped.lower()


@pytest.mark.asyncio
async def test_gateway_loop_goal_note_when_goal_active(loop_env):
    from hermes_cli.goals import GoalManager

    GoalManager(session_id="sid-gateway-loop").set("finish the migration")
    runner = _make_runner()
    response = await GatewayRunner._handle_loop_command(runner, _make_event("/loop 5m poll CI"))
    assert "active /goal" in response


@pytest.mark.asyncio
async def test_post_turn_loop_completion_completes_inflight_tick(loop_env):
    runner = _make_runner()
    await GatewayRunner._handle_loop_command(runner, _make_event("/loop 5m poll CI"))

    mgr = loops.LoopManager(session_id="sid-gateway-loop")
    mgr.state.next_due_at = time.time() - 1
    assert mgr.fire_tick() is not None

    entry = _FakeSessionEntry()
    await GatewayRunner._post_turn_loop_completion(
        runner,
        session_entry=entry,
        source=None,
        final_response="CI is done.\nLOOP_COMPLETE",
    )
    reloaded = loops.load_loop("sid-gateway-loop")
    assert reloaded.status == "done"


@pytest.mark.asyncio
async def test_post_turn_loop_completion_noop_without_inflight_tick(loop_env):
    runner = _make_runner()
    await GatewayRunner._handle_loop_command(runner, _make_event("/loop 5m poll CI"))
    entry = _FakeSessionEntry()
    # No tick fired — the ordinary user turn must not consume loop state.
    await GatewayRunner._post_turn_loop_completion(
        runner,
        session_entry=entry,
        source=None,
        final_response="regular reply LOOP_COMPLETE",
    )
    reloaded = loops.load_loop("sid-gateway-loop")
    assert reloaded.status == "active"
    assert reloaded.ticks_fired == 0


def test_streamed_already_sent_none_recovers_text_for_hooks():
    """Streamed turns return None. Hooks must still see the delivered reply."""
    event = _make_event("wakeup")
    event._streamed_final_response = "CI is green.\nLOOP_COMPLETE"
    assert GatewayRunner._final_text_for_post_turn_hooks(None, event) == (
        "CI is green.\nLOOP_COMPLETE"
    )
    assert GatewayRunner._final_text_for_post_turn_hooks(None, _make_event("x")) == ""
    assert (
        GatewayRunner._final_text_for_post_turn_hooks(
            {"final_response": "from dict"}, event
        )
        == "from dict"
    )


@pytest.mark.asyncio
async def test_streamed_already_sent_completes_loop_tick(loop_env):
    """A streamed wakeup must not leave awaiting_response stuck."""
    runner = _make_runner()
    await GatewayRunner._handle_loop_command(runner, _make_event("/loop 5m poll CI"))

    mgr = loops.LoopManager(session_id="sid-gateway-loop")
    mgr.state.next_due_at = time.time() - 1
    assert mgr.fire_tick() is not None
    assert mgr.state.awaiting_response is True
    assert mgr.is_due() is False

    event = _make_event("wakeup")
    event._streamed_final_response = "CI is done.\nLOOP_COMPLETE"
    # Same inputs the already_sent branch leaves for _handle_message.
    final_text = GatewayRunner._final_text_for_post_turn_hooks(None, event)
    assert final_text.strip()

    await GatewayRunner._post_turn_loop_completion(
        runner,
        session_entry=_FakeSessionEntry(),
        source=None,
        final_response=final_text,
    )
    reloaded = loops.load_loop("sid-gateway-loop")
    assert reloaded.awaiting_response is False
    assert reloaded.status == "done"


@pytest.mark.asyncio
async def test_empty_agent_result_releases_inflight_loop_tick(loop_env):
    runner = _make_runner()
    await GatewayRunner._handle_loop_command(runner, _make_event("/loop 5m poll CI"))

    mgr = loops.LoopManager(session_id="sid-gateway-loop")
    mgr.state.next_due_at = time.time() - 1
    assert mgr.fire_tick() is not None
    assert mgr.state.awaiting_response is True

    runner._post_turn_goal_continuation = AsyncMock()
    await GatewayRunner._run_post_turn_hooks(
        runner,
        agent_result={"final_response": ""},
        source=_make_event("wakeup").source,
        is_internal=True,
    )

    runner._post_turn_goal_continuation.assert_not_awaited()
    reloaded = loops.load_loop("sid-gateway-loop")
    assert reloaded.awaiting_response is False
    assert reloaded.status == "active"
    assert reloaded.next_due_at > time.time()


@pytest.mark.asyncio
async def test_goal_hook_failure_does_not_block_loop_completion(loop_env, caplog):
    runner = _make_runner()
    await GatewayRunner._handle_loop_command(runner, _make_event("/loop 5m poll CI"))

    mgr = loops.LoopManager(session_id="sid-gateway-loop")
    mgr.state.next_due_at = time.time() - 1
    assert mgr.fire_tick() is not None

    runner._post_turn_goal_continuation = AsyncMock(side_effect=RuntimeError("judge failed"))
    with caplog.at_level(logging.DEBUG, logger="gateway.run"):
        await GatewayRunner._run_post_turn_hooks(
            runner,
            agent_result={"final_response": "still working"},
            source=_make_event("wakeup").source,
            is_internal=True,
        )

    reloaded = loops.load_loop("sid-gateway-loop")
    assert reloaded.awaiting_response is False
    assert "goal continuation hook failed: judge failed" in caplog.text


@pytest.mark.asyncio
async def test_post_turn_session_resolution_failure_is_logged(loop_env, caplog):
    runner = _make_runner()
    runner.session_store.get_or_create_session = Mock(side_effect=RuntimeError("store unavailable"))

    with caplog.at_level(logging.DEBUG, logger="gateway.run"):
        await GatewayRunner._run_post_turn_hooks(
            runner,
            agent_result={"final_response": ""},
            source=_make_event("wakeup").source,
            is_internal=True,
        )

    assert "post-turn session resolution failed: store unavailable" in caplog.text
