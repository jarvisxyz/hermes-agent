"""Tests for Slack Block Kit clarify buttons and rich message rendering.

Mirrors test_slack_approval_buttons.py for the new ``send_clarify`` and
Block Kit action handlers, plus unit tests for the ``_markdown_to_blocks``
utility and the ``_build_progress_blocks`` helper.
"""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the repo root is importable
# ---------------------------------------------------------------------------
_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


# ---------------------------------------------------------------------------
# Minimal Slack SDK mock so SlackAdapter can be imported
# ---------------------------------------------------------------------------
def _ensure_slack_mock():
    """Wire up the minimal mocks required to import SlackAdapter."""
    if "slack_bolt" in sys.modules:
        return
    slack_bolt = MagicMock()
    slack_bolt.async_app.AsyncApp = MagicMock
    sys.modules["slack_bolt"] = slack_bolt
    sys.modules["slack_bolt.async_app"] = slack_bolt.async_app
    handler_mod = MagicMock()
    handler_mod.AsyncSocketModeHandler = MagicMock
    sys.modules["slack_bolt.adapter"] = MagicMock()
    sys.modules["slack_bolt.adapter.socket_mode"] = MagicMock()
    sys.modules["slack_bolt.adapter.socket_mode.async_handler"] = handler_mod
    sdk_mod = MagicMock()
    sdk_mod.web = MagicMock()
    sdk_mod.web.async_client = MagicMock()
    sdk_mod.web.async_client.AsyncWebClient = MagicMock
    sys.modules["slack_sdk"] = sdk_mod
    sys.modules["slack_sdk.web"] = sdk_mod.web
    sys.modules["slack_sdk.web.async_client"] = sdk_mod.web.async_client


_ensure_slack_mock()

from gateway.platforms.slack import SlackAdapter, _markdown_to_blocks
from gateway.config import PlatformConfig


def _make_adapter(extra=None):
    """Create a SlackAdapter instance with mocked internals."""
    config = PlatformConfig(enabled=True, token="***", extra=extra or {})
    adapter = SlackAdapter(config)
    adapter._app = MagicMock()
    adapter._bot_user_id = "U_BOT"
    adapter._team_clients = {"T1": AsyncMock()}
    adapter._team_bot_user_ids = {"T1": "U_BOT"}
    adapter._channel_team = {"C1": "T1"}
    return adapter


def _clear_clarify_state():
    from tools import clarify_gateway as cm

    with cm._lock:
        cm._entries.clear()
        cm._session_index.clear()
        cm._notify_cbs.clear()


# ===========================================================================
# send_clarify — Block Kit render
# ===========================================================================


class TestSlackSendClarify:
    """Verify the rendered prompt has Block Kit buttons or falls back."""

    def setup_method(self):
        _clear_clarify_state()

    @pytest.mark.asyncio
    async def test_multi_choice_renders_blocks_with_buttons(self):
        adapter = _make_adapter()
        mock_client = adapter._team_clients["T1"]
        mock_client.chat_postMessage = AsyncMock(return_value={"ts": "1234.5678"})

        result = await adapter.send_clarify(
            chat_id="C1",
            question="Which option?",
            choices=["alpha", "beta", "gamma"],
            clarify_id="cid1",
            session_key="sk1",
        )

        assert result.success is True
        assert result.message_id == "1234.5678"

        kwargs = mock_client.chat_postMessage.call_args[1]
        assert kwargs["channel"] == "C1"
        blocks = kwargs["blocks"]
        # Section block with question
        section = blocks[0]
        assert section["type"] == "section"
        assert "Which option?" in section["text"]["text"]
        # Actions block with 3 choice buttons + Other
        actions = blocks[1]
        assert actions["type"] == "actions"
        assert len(actions["elements"]) == 4  # 3 choices + Other
        # Verify button values encode clarify_id:index
        choice_buttons = actions["elements"][:3]
        for i, btn in enumerate(choice_buttons):
            assert btn["action_id"] == "hermes_clarify_choice"
            assert btn["value"] == f"cid1:{i}"
        # "Other" button
        other_btn = actions["elements"][3]
        assert other_btn["action_id"] == "hermes_clarify_other"
        assert other_btn["value"] == "cid1"

        # State populated
        assert adapter._clarify_state["cid1"] == "sk1"
        assert adapter._clarify_choices["cid1"] == ["alpha", "beta", "gamma"]

    @pytest.mark.asyncio
    async def test_multi_choice_many_options_fallback(self):
        """More than 5 choices → numbered list + Other button only."""
        adapter = _make_adapter()
        mock_client = adapter._team_clients["T1"]
        mock_client.chat_postMessage = AsyncMock(return_value={"ts": "1234.5679"})

        choices = [f"opt{i}" for i in range(8)]
        result = await adapter.send_clarify(
            chat_id="C1",
            question="Pick one",
            choices=choices,
            clarify_id="cid_many",
            session_key="sk_many",
        )

        assert result.success is True
        kwargs = mock_client.chat_postMessage.call_args[1]
        blocks = kwargs["blocks"]
        # Section block should have numbered list
        section_text = blocks[0]["text"]["text"]
        assert "1. opt0" in section_text
        assert "8. opt7" in section_text
        # Actions block should only have the "Other" button
        actions = blocks[1]
        assert len(actions["elements"]) == 1
        assert actions["elements"][0]["action_id"] == "hermes_clarify_other"

    @pytest.mark.asyncio
    async def test_open_ended_sends_plain_text(self):
        """No choices → falls back to self.send() for plain text."""
        adapter = _make_adapter()
        adapter.send = AsyncMock(
            return_value=MagicMock(success=True, message_id="ts_plain")
        )

        result = await adapter.send_clarify(
            chat_id="C1",
            question="What is your name?",
            choices=None,
            clarify_id="cid_open",
            session_key="sk_open",
        )

        assert result.success is True
        adapter.send.assert_called_once()
        call_kwargs = adapter.send.call_args[1]
        assert "What is your name?" in call_kwargs["content"]

    @pytest.mark.asyncio
    async def test_not_connected(self):
        adapter = _make_adapter()
        adapter._app = None
        result = await adapter.send_clarify(
            chat_id="C1",
            question="?",
            choices=["a"],
            clarify_id="cid_nc",
            session_key="sk_nc",
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_button_values_encode_clarify_id_and_index(self):
        adapter = _make_adapter()
        mock_client = adapter._team_clients["T1"]
        mock_client.chat_postMessage = AsyncMock(return_value={"ts": "ts1"})

        await adapter.send_clarify(
            chat_id="C1",
            question="?",
            choices=["x", "y", "z"],
            clarify_id="myid",
            session_key="sk",
        )

        kwargs = mock_client.chat_postMessage.call_args[1]
        actions = kwargs["blocks"][1]
        values = [e["value"] for e in actions["elements"][:3]]
        assert values == ["myid:0", "myid:1", "myid:2"]


# ===========================================================================
# _handle_clarify_choice_action — button callback
# ===========================================================================


class TestSlackClarifyChoiceAction:
    """Verify clarify choice button clicks resolve correctly."""

    def setup_method(self):
        _clear_clarify_state()

    @pytest.mark.asyncio
    async def test_choice_resolves_clarify(self):
        adapter = _make_adapter()
        adapter._clarify_state["cid1"] = "sk1"
        adapter._clarify_choices["cid1"] = ["alpha", "beta", "gamma"]

        mock_client = adapter._team_clients["T1"]
        mock_client.chat_update = AsyncMock(return_value={"ok": True})

        ack = AsyncMock()
        body = {
            "message": {
                "ts": "ts_choice",
                "blocks": [
                    {
                        "type": "section",
                        "text": {"text": "❓ *Which?*"},
                    }
                ],
            },
            "channel": {"id": "C1"},
            "user": {"id": "U_USER", "name": "testuser"},
        }
        action = {"action_id": "hermes_clarify_choice", "value": "cid1:1"}

        with patch(
            "tools.clarify_gateway.resolve_gateway_clarify"
        ) as mock_resolve:
            await adapter._handle_clarify_choice_action(ack, body, action)
            mock_resolve.assert_called_once_with("cid1", "beta")

        ack.assert_called_once()
        # Message updated to show selection
        mock_client.chat_update.assert_called_once()
        update_kwargs = mock_client.chat_update.call_args[1]
        assert "Selected" in update_kwargs["text"]

    @pytest.mark.asyncio
    async def test_updates_message_removes_buttons(self):
        adapter = _make_adapter()
        adapter._clarify_state["cid2"] = "sk2"
        adapter._clarify_choices["cid2"] = ["a", "b"]

        mock_client = adapter._team_clients["T1"]
        mock_client.chat_update = AsyncMock(return_value={"ok": True})

        ack = AsyncMock()
        body = {
            "message": {
                "ts": "ts_upd",
                "blocks": [{"type": "section", "text": {"text": "❓ *Q*"}}],
            },
            "channel": {"id": "C1"},
            "user": {"id": "U_USER", "name": "tester"},
        }
        action = {"action_id": "hermes_clarify_choice", "value": "cid2:0"}

        with patch("tools.clarify_gateway.resolve_gateway_clarify"):
            await adapter._handle_clarify_choice_action(ack, body, action)

        update_kwargs = mock_client.chat_update.call_args[1]
        blocks = update_kwargs["blocks"]
        # Should have section + context, NO actions block
        block_types = [b["type"] for b in blocks]
        assert "actions" not in block_types
        assert "context" in block_types

    @pytest.mark.asyncio
    async def test_malformed_value_ignored(self):
        adapter = _make_adapter()
        ack = AsyncMock()
        body = {
            "message": {"ts": "ts_m", "blocks": []},
            "channel": {"id": "C1"},
            "user": {"id": "U_USER", "name": "test"},
        }
        action = {"action_id": "hermes_clarify_choice", "value": "nocolon"}

        with patch("tools.clarify_gateway.resolve_gateway_clarify") as mock_resolve:
            await adapter._handle_clarify_choice_action(ack, body, action)
            mock_resolve.assert_not_called()

    @pytest.mark.asyncio
    async def test_unauthorized_user_ignored(self):
        adapter = _make_adapter()
        adapter._clarify_state["cid_auth"] = "sk_auth"
        adapter._clarify_choices["cid_auth"] = ["a", "b"]

        ack = AsyncMock()
        body = {
            "message": {"ts": "ts_auth", "blocks": []},
            "channel": {"id": "C1"},
            "user": {"id": "U_EVIL", "name": "eviluser"},
        }
        action = {"action_id": "hermes_clarify_choice", "value": "cid_auth:0"}

        with patch.dict(os.environ, {"SLACK_ALLOWED_USERS": "U_GOOD"}):
            with patch(
                "tools.clarify_gateway.resolve_gateway_clarify"
            ) as mock_resolve:
                await adapter._handle_clarify_choice_action(ack, body, action)
                mock_resolve.assert_not_called()


# ===========================================================================
# _handle_clarify_other_action — "Other" button callback
# ===========================================================================


class TestSlackClarifyOtherAction:
    """Verify the 'Other' button triggers text-capture mode."""

    def setup_method(self):
        _clear_clarify_state()

    @pytest.mark.asyncio
    async def test_other_marks_awaiting_text(self):
        adapter = _make_adapter()
        adapter._clarify_state["cid_other"] = "sk_other"
        adapter._clarify_choices["cid_other"] = ["a", "b"]

        mock_client = adapter._team_clients["T1"]
        mock_client.chat_update = AsyncMock(return_value={"ok": True})

        ack = AsyncMock()
        body = {
            "message": {
                "ts": "ts_other",
                "blocks": [{"type": "section", "text": {"text": "❓ *Q*"}}],
            },
            "channel": {"id": "C1"},
            "user": {"id": "U_USER", "name": "testuser"},
        }
        action = {"action_id": "hermes_clarify_other", "value": "cid_other"}

        with patch("tools.clarify_gateway.mark_awaiting_text") as mock_mark:
            await adapter._handle_clarify_other_action(ack, body, action)
            mock_mark.assert_called_once_with("cid_other")

        ack.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_message_shows_waiting(self):
        adapter = _make_adapter()
        adapter._clarify_state["cid_wait"] = "sk_wait"
        adapter._clarify_choices["cid_wait"] = ["x"]

        mock_client = adapter._team_clients["T1"]
        mock_client.chat_update = AsyncMock(return_value={"ok": True})

        ack = AsyncMock()
        body = {
            "message": {
                "ts": "ts_wait",
                "blocks": [{"type": "section", "text": {"text": "❓ *Q*"}}],
            },
            "channel": {"id": "C1"},
            "user": {"id": "U_USER", "name": "tester"},
        }
        action = {"action_id": "hermes_clarify_other", "value": "cid_wait"}

        with patch("tools.clarify_gateway.mark_awaiting_text"):
            await adapter._handle_clarify_other_action(ack, body, action)

        mock_client.chat_update.assert_called_once()
        update_kwargs = mock_client.chat_update.call_args[1]
        assert "Waiting" in update_kwargs["text"]


# ===========================================================================
# _markdown_to_blocks — unit tests
# ===========================================================================


class TestMarkdownToBlocks:
    """Verify the markdown-to-Block-Kit conversion utility."""

    def test_empty_content(self):
        assert _markdown_to_blocks("") == []

    def test_simple_text(self):
        blocks = _markdown_to_blocks("Just a simple message")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "section"
        assert blocks[0]["text"]["text"] == "Just a simple message"

    def test_headers_create_multiple_sections(self):
        content = "## Section A\n\nContent A\n\n## Section B\n\nContent B"
        blocks = _markdown_to_blocks(content)
        # Should have: section, divider, section
        assert len(blocks) == 3
        assert blocks[0]["type"] == "section"
        assert blocks[1]["type"] == "divider"
        assert blocks[2]["type"] == "section"
        # H2 becomes bold
        assert "*Section A*" in blocks[0]["text"]["text"]
        assert "*Section B*" in blocks[2]["text"]["text"]

    def test_h3_becomes_italic(self):
        blocks = _markdown_to_blocks("### Minor Header\n\nDetails")
        assert len(blocks) == 1
        assert blocks[0]["text"]["text"].startswith("_")

    def test_max_50_blocks(self):
        # 60 headers → should be capped at 50 blocks
        content = "\n".join(f"## H{i}\n\nBody {i}" for i in range(60))
        blocks = _markdown_to_blocks(content)
        assert len(blocks) <= 50

    def test_truncation(self):
        # Very long section text
        long_text = "x" * 5000
        blocks = _markdown_to_blocks(long_text)
        assert len(blocks) == 1
        assert len(blocks[0]["text"]["text"]) <= 3000

    def test_single_header_no_divider(self):
        """A single header section produces 1 block, no divider."""
        blocks = _markdown_to_blocks("## Only Header\n\nSome text")
        assert len(blocks) == 1

    def test_header_strips_inner_bold_markers(self):
        """## **Bold Title** → *Bold Title* (not ***Bold Title***)."""
        blocks = _markdown_to_blocks("## **Bold Title**\n\nText")
        text = blocks[0]["text"]["text"]
        assert "***" not in text


# ===========================================================================
# _build_progress_blocks — unit tests
# ===========================================================================


class TestBuildProgressBlocks:
    """Verify the progress block builder utility."""

    def test_empty_lines(self):
        assert SlackAdapter._build_progress_blocks([]) == []

    def test_single_line(self):
        blocks = SlackAdapter._build_progress_blocks(["🔧 Running tool"])
        assert len(blocks) == 2
        assert blocks[0]["type"] == "section"
        assert "🔧 Running tool" in blocks[0]["text"]["text"]
        assert blocks[1]["type"] == "context"
        assert "Running tools" in blocks[1]["elements"][0]["text"]

    def test_multiple_lines(self):
        blocks = SlackAdapter._build_progress_blocks(
            ["🔧 Tool A", "🔧 Tool B", "🔧 Tool C"]
        )
        section_text = blocks[0]["text"]["text"]
        assert "Tool A" in section_text
        assert "Tool B" in section_text
        assert "Tool C" in section_text

    def test_truncation(self):
        long_lines = ["x" * 2000, "y" * 2000]
        blocks = SlackAdapter._build_progress_blocks(long_lines, max_chars=100)
        assert len(blocks[0]["text"]["text"]) <= 103  # 100 + "..."
