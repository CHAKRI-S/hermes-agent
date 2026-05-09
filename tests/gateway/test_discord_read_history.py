"""Tests for Discord /readXX history injection commands."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import sys

import pytest

from gateway.config import PlatformConfig


def _ensure_discord_mock():
    if "discord" in sys.modules and hasattr(sys.modules["discord"], "__file__"):
        return

    discord_mod = sys.modules.get("discord") or MagicMock()
    discord_mod.Intents.default.return_value = MagicMock()
    discord_mod.DMChannel = type("DMChannel", (), {})
    discord_mod.Thread = type("Thread", (), {})
    discord_mod.ForumChannel = type("ForumChannel", (), {})
    discord_mod.Interaction = object

    class _FakeCommand:
        def __init__(self, *, name, description, callback, parent=None):
            self.name = name
            self.description = description
            self.callback = callback
            self.parent = parent

    discord_mod.app_commands = SimpleNamespace(
        describe=lambda **kwargs: (lambda fn: fn),
        choices=lambda **kwargs: (lambda fn: fn),
        autocomplete=lambda **kwargs: (lambda fn: fn),
        Choice=lambda **kwargs: SimpleNamespace(**kwargs),
        Command=_FakeCommand,
        Group=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    ext_mod = MagicMock()
    commands_mod = MagicMock()
    commands_mod.Bot = MagicMock
    ext_mod.commands = commands_mod

    sys.modules["discord"] = discord_mod
    sys.modules.setdefault("discord.ext", ext_mod)
    sys.modules.setdefault("discord.ext.commands", commands_mod)


_ensure_discord_mock()

from plugins.platforms.discord.adapter import DiscordAdapter  # noqa: E402


class FakeHistoryChannel:
    id = 123
    name = "control"

    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def history(self, **kwargs):
        self.calls.append(kwargs)

        async def _aiter():
            for msg in self.messages:
                yield msg

        return _aiter()


class FakeThreadReadChannel(FakeHistoryChannel):
    def __init__(self, messages):
        super().__init__(messages)
        self.sent = []
        self.created_threads = []

    async def create_thread(self, **kwargs):
        self.created_threads.append(kwargs)
        return SimpleNamespace(id=456, name=kwargs.get("name") or "Thread")

    async def send(self, content):
        self.sent.append(content)
        return SimpleNamespace(id=789, content=content)


def _msg(content, name="Tik", *, bot=False, kind="default", mid=1):
    return SimpleNamespace(
        id=mid,
        content=content,
        clean_content=content,
        author=SimpleNamespace(display_name=name, name=name, bot=bot),
        created_at=datetime(2026, 5, 8, 12, 0, mid, tzinfo=timezone.utc),
        type=SimpleNamespace(name=kind),
        attachments=[],
    )


@pytest.fixture
def adapter():
    config = PlatformConfig(enabled=True, token="***")
    return DiscordAdapter(config)


def test_parse_fixed_read_history_commands(adapter):
    assert adapter._parse_read_history_command("/read20 สรุปให้หน่อย") == (20, "สรุปให้หน่อย")
    assert adapter._parse_read_history_command("/read50") == (50, "")
    assert adapter._parse_read_history_command("/read100 มี task ค้างไหม") == (100, "มี task ค้างไหม")
    assert adapter._parse_read_history_command("/read200") == (200, "")
    assert adapter._parse_read_history_command("/read prompt: สรุปจากจุดนี้") == (200, "สรุปจากจุดนี้")
    assert adapter._parse_read_history_command("/read limit: 500 prompt: สรุปย้อนหลังไกลหน่อย") == (
        500,
        "สรุปย้อนหลังไกลหน่อย",
    )
    assert adapter._parse_read_history_command("/read 1000 สรุปทั้งหมด") == (1000, "สรุปทั้งหมด")
    assert adapter._parse_read_history_command("/read 50") == (50, "")
    assert adapter._parse_read_history_command("/read500") == (500, "")
    assert adapter._parse_read_history_command("/read limit: 999") is None


def test_parse_threadread200_text_command(adapter):
    assert adapter._parse_thread_read_command('/threadread200 name: "Planning Context"') == (
        200,
        "Planning Context",
        "",
    )
    assert adapter._parse_thread_read_command(
        '/threadread200 name: "Planning Context" สรุป decision ด้วย'
    ) == (200, "Planning Context", "สรุป decision ด้วย")
    assert adapter._parse_thread_read_command(
        '/threadread name: "Planning Context" limit: 500 prompt: สรุป decision ด้วย'
    ) == (500, "Planning Context", "สรุป decision ด้วย")
    assert adapter._parse_thread_read_command(
        '/threadread name: "Planning Context" prompt: สรุป decision ด้วย'
    ) == (200, "Planning Context", "สรุป decision ด้วย")
    assert adapter._parse_thread_read_command('/threadread50 name: "nope"') == (50, "nope", "")
    assert adapter._parse_thread_read_command("/threadread200 Planning Context") is None


@pytest.mark.asyncio
async def test_read_history_injection_fetches_current_channel_and_skips_bots(adapter):
    channel = FakeHistoryChannel([
        _msg("ข้อความล่าสุด", "Tik", mid=3),
        _msg("bot noise", "Hermes", bot=True, mid=2),
        _msg("รายละเอียดงาน", "Somchai", mid=1),
    ])
    current = SimpleNamespace(created_at=datetime(2026, 5, 8, 12, 1, 0, tzinfo=timezone.utc))

    text = await adapter._inject_read_history_context(
        "/read50 จากที่คุยกันควรตอบว่าไง",
        channel=channel,
        before=current,
    )

    assert channel.calls == [{"limit": 51, "before": current}]
    assert "[Discord history: last 50 messages from #control]" in text
    assert "Tik: ข้อความล่าสุด" in text
    assert "Somchai: รายละเอียดงาน" in text
    assert "bot noise" not in text
    assert "Skipped 1 bot/system message" in text
    assert "User question:\nจากที่คุยกันควรตอบว่าไง" in text
    assert not text.startswith("/read50")


@pytest.mark.asyncio
async def test_read_history_reply_anchor_reads_up_to_replied_message(adapter):
    channel = FakeHistoryChannel([
        _msg("ก่อน anchor หนึ่ง", "Somchai", mid=2),
        _msg("ก่อน anchor สอง", "Tik", mid=1),
    ])
    anchor = _msg("ข้อความที่ reply ถึง", "Tik", mid=3)
    current = SimpleNamespace(created_at=datetime(2026, 5, 8, 12, 1, 0, tzinfo=timezone.utc))

    text = await adapter._inject_read_history_context(
        "/read50 prompt: สรุปจากข้อความนี้ย้อนหลัง",
        channel=channel,
        before=current,
        anchor=anchor,
    )

    assert channel.calls == [{"limit": 49, "before": anchor}]
    assert "[Discord history: last 50 messages up to replied message from #control]" in text
    assert "Somchai: ก่อน anchor หนึ่ง" in text
    assert "Tik: ข้อความที่ reply ถึง" in text
    assert "User question:\nสรุปจากข้อความนี้ย้อนหลัง" in text


@pytest.mark.asyncio
async def test_read_history_without_question_asks_for_summary(adapter):
    channel = FakeHistoryChannel([_msg("คุยเรื่อง read50", "Tik", mid=1)])

    text = await adapter._inject_read_history_context("/read20", channel=channel)

    assert "last 20 messages" in text
    assert "User question:\nสรุป context ล่าสุดจากข้อความย้อนหลังด้านบนให้หน่อย" in text


@pytest.mark.asyncio
async def test_native_read50_slash_uses_history_instead_of_simple_command(adapter):
    adapter._check_slash_authorization = AsyncMock(return_value=True)
    adapter.handle_message = AsyncMock()

    channel = FakeHistoryChannel([_msg("คุยเรื่อง deploy", "Tik", mid=1)])
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42, name="Tik", display_name="Tik"),
        channel=channel,
        channel_id=123,
        guild_id=999,
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )

    await adapter._run_read_history_slash(interaction, "read50", "สรุปให้หน่อย")

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.edit_original_response.assert_awaited_once()
    args, kwargs = interaction.edit_original_response.await_args
    assert "Read 50 Discord message(s)" in kwargs["content"]
    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.message_type.name == "TEXT"
    assert "[Discord history: last 50 messages from #control]" in event.text
    assert "User question:\nสรุปให้หน่อย" in event.text


@pytest.mark.asyncio
async def test_native_read200_slash_injects_last_200_history_messages(adapter):
    adapter._check_slash_authorization = AsyncMock(return_value=True)
    adapter.handle_message = AsyncMock()

    channel = FakeHistoryChannel([_msg("สรุปก่อนเปิด thread", "Tik", mid=1)])
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42, name="Tik", display_name="Tik"),
        channel=channel,
        channel_id=123,
        guild_id=999,
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )

    await adapter._run_read_history_slash(interaction, "read200", "อ่านแล้วสรุป")

    interaction.edit_original_response.assert_awaited_once()
    args, kwargs = interaction.edit_original_response.await_args
    assert "Read 200 Discord message(s)" in kwargs["content"]
    assert channel.calls == [{"limit": 201}]
    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.message_type.name == "TEXT"
    assert "[Discord history: last 200 messages from #control]" in event.text
    assert "Tik: สรุปก่อนเปิด thread" in event.text
    assert "User question:\nอ่านแล้วสรุป" in event.text


@pytest.mark.asyncio
async def test_native_threadread_posts_persistent_parent_ack(adapter):
    adapter._check_slash_authorization = AsyncMock(return_value=True)
    adapter._dispatch_thread_session = AsyncMock()

    channel = FakeThreadReadChannel([_msg("บริบทก่อนเปิด thread", "Tik", mid=1)])
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42, name="Tik", display_name="Tik"),
        channel=channel,
        channel_id=123,
        guild=SimpleNamespace(name="Guild"),
        guild_id=999,
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )

    await adapter._handle_thread_read_slash(
        interaction,
        limit=200,
        name="Planning Context",
        prompt="สรุปต่อใน thread",
    )

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.edit_original_response.assert_awaited_once()
    assert channel.created_threads
    assert channel.sent == [
        "🧵 Created thread <#456> and seeded it with /read200 context from this channel."
    ]
    adapter._dispatch_thread_session.assert_awaited_once()
    dispatched_text = adapter._dispatch_thread_session.await_args.args[3]
    assert "[Discord history: last 200 messages from #control]" in dispatched_text
    assert "Tik: บริบทก่อนเปิด thread" in dispatched_text


@pytest.mark.asyncio
async def test_text_threadread_fallback_posts_persistent_parent_ack(adapter):
    adapter._dispatch_thread_session = AsyncMock()

    channel = FakeThreadReadChannel([_msg("ข้อความเก่าที่ต้องอ่าน", "Tik", mid=1)])
    message = SimpleNamespace(
        author=SimpleNamespace(id=42, name="Tik", display_name="Tik"),
        channel=channel,
        guild=SimpleNamespace(name="Guild"),
    )

    handled = await adapter._handle_thread_read_message(
        message,
        limit=100,
        name="Thread from text command",
        prompt="สรุป context",
    )

    assert handled is True
    assert channel.sent == [
        "🧵 Created thread <#456> and seeded it with /read100 context from this channel."
    ]
    adapter._dispatch_thread_session.assert_awaited_once()
