"""Tests for Slack Block Kit interactive action handlers and send_clarify.

Covers: clarify choice/other buttons, thinking toggle, model selector,
reasoning level selector, and the send_clarify method.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure repo root importable
# ---------------------------------------------------------------------------
_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


# ---------------------------------------------------------------------------
# Mock slack-bolt so SlackAdapter can be imported
# ---------------------------------------------------------------------------
def _ensure_slack_mock():
    if "slack_bolt" in sys.modules and hasattr(sys.modules["slack_bolt"], "__file__"):
        return
    slack_bolt = MagicMock()
    slack_bolt.async_app.AsyncApp = MagicMock
    handler_mod = MagicMock()
    handler_mod.AsyncSocketModeHandler = MagicMock
    sys.modules["slack_bolt"] = slack_bolt
    sys.modules["slack_bolt.async_app"] = slack_bolt.async_app
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

from gateway.platforms.slack import SlackAdapter
from gateway.config import PlatformConfig


def _make_adapter():
    """Create a SlackAdapter with mocked internals."""
    config = PlatformConfig(enabled=True, token="***")
    adapter = SlackAdapter(config)
    adapter._app = MagicMock()
    adapter._bot_user_id = "U_BOT"
    adapter._team_clients = {"T1": AsyncMock()}
    adapter._team_bot_user_ids = {"T1": "U_BOT"}
    adapter._channel_team = {"C1": "T1"}
    return adapter


# ===========================================================================
# send_clarify
# ===========================================================================


class TestSendClarify:
    @pytest.mark.asyncio
    async def test_multi_choice_sends_blocks(self):
        adapter = _make_adapter()
        mock_client = adapter._team_clients["T1"]
        mock_client.chat_postMessage = AsyncMock(return_value={"ts": "10.20"})

        result = await adapter.send_clarify(
            chat_id="C1",
            question="Which approach?",
            choices=["Fast", "Thorough", "Balanced"],
            clarify_id="cl-001",
            session_key="agent:main:slack:group:C1:1111",
        )

        assert result.success is True
        assert result.message_id == "10.20"
        kwargs = mock_client.chat_postMessage.call_args[1]
        assert "blocks" in kwargs
        blocks = kwargs["blocks"]
        assert blocks[0]["type"] == "section"
        assert "Which approach?" in blocks[0]["text"]["text"]
        assert blocks[1]["type"] == "actions"
        # 3 choices + "Other" = 4 buttons
        assert len(blocks[1]["elements"]) == 4

        # State tracked
        assert adapter._clarify_state["cl-001"] == "agent:main:slack:group:C1:1111"
        assert adapter._clarify_choices["cl-001"] == ["Fast", "Thorough", "Balanced"]

    @pytest.mark.asyncio
    async def test_open_ended_falls_back_to_send(self):
        adapter = _make_adapter()
        mock_client = adapter._team_clients["T1"]
        mock_client.chat_postMessage = AsyncMock(return_value={"ts": "11.22"})

        result = await adapter.send_clarify(
            chat_id="C1",
            question="What do you think?",
            choices=None,
            clarify_id="cl-002",
            session_key="agent:main",
        )

        assert result.success is True
        # Should use plain text send, no blocks
        kwargs = mock_client.chat_postMessage.call_args[1]
        assert "blocks" not in kwargs or not kwargs.get("blocks")
        assert "What do you think?" in kwargs.get("text", "")

    @pytest.mark.asyncio
    async def test_not_connected(self):
        adapter = _make_adapter()
        adapter._app = None
        result = await adapter.send_clarify(
            chat_id="C1", question="Q", choices=["A"], clarify_id="c", session_key="s"
        )
        assert result.success is False


# ===========================================================================
# _handle_clarify_choice_action
# ===========================================================================


class TestClarifyChoiceAction:
    @pytest.mark.asyncio
    async def test_resolves_choice(self):
        adapter = _make_adapter()
        # Set up state
        adapter._clarify_state["cl-100"] = "agent:main:slack:group:C1:1111"
        adapter._clarify_choices["cl-100"] = ["Option A", "Option B", "Option C"]

        ack = AsyncMock()
        body = {
            "message": {"ts": "10.20", "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": "Which?"}},
                {"type": "actions", "elements": []},
            ]},
            "channel": {"id": "C1"},
            "user": {"name": "alice", "id": "U_ALICE"},
        }
        action = {"action_id": "hermes_clarify_choice", "value": "cl-100:1"}

        mock_client = adapter._team_clients["T1"]
        mock_client.chat_update = AsyncMock()

        with patch("tools.clarify_gateway.resolve_gateway_clarify") as mock_resolve:
            await adapter._handle_clarify_choice_action(ack, body, action)

        ack.assert_called_once()
        mock_resolve.assert_called_once_with("cl-100", "Option B")
        # Message updated
        mock_client.chat_update.assert_called_once()
        update_kwargs = mock_client.chat_update.call_args[1]
        assert "alice" in update_kwargs["text"]
        # State cleaned up
        assert "cl-100" not in adapter._clarify_state
        assert "cl-100" not in adapter._clarify_choices

    @pytest.mark.asyncio
    async def test_malformed_value(self):
        adapter = _make_adapter()
        ack = AsyncMock()
        body = {
            "message": {"ts": "1.0", "blocks": []},
            "channel": {"id": "C1"},
            "user": {"name": "bob", "id": "U_BOB"},
        }
        action = {"action_id": "hermes_clarify_choice", "value": "no-colon-here"}

        with patch("tools.clarify_gateway.resolve_gateway_clarify") as mock_resolve:
            await adapter._handle_clarify_choice_action(ack, body, action)

        ack.assert_called_once()
        mock_resolve.assert_not_called()

    @pytest.mark.asyncio
    async def test_out_of_range_index(self):
        adapter = _make_adapter()
        adapter._clarify_state["cl-200"] = "session"
        adapter._clarify_choices["cl-200"] = ["Only One"]

        ack = AsyncMock()
        body = {
            "message": {"ts": "1.0", "blocks": []},
            "channel": {"id": "C1"},
            "user": {"name": "carol", "id": "U_CAROL"},
        }
        action = {"action_id": "hermes_clarify_choice", "value": "cl-200:5"}

        with patch("tools.clarify_gateway.resolve_gateway_clarify") as mock_resolve:
            await adapter._handle_clarify_choice_action(ack, body, action)

        mock_resolve.assert_not_called()


# ===========================================================================
# _handle_clarify_other_action
# ===========================================================================


class TestClarifyOtherAction:
    @pytest.mark.asyncio
    async def test_marks_awaiting_text(self):
        adapter = _make_adapter()
        ack = AsyncMock()
        body = {
            "message": {"ts": "10.20", "blocks": []},
            "channel": {"id": "C1"},
            "user": {"name": "alice", "id": "U_ALICE"},
        }
        action = {"action_id": "hermes_clarify_other", "value": "cl-300"}

        mock_client = adapter._team_clients["T1"]
        mock_client.chat_update = AsyncMock()

        with patch("tools.clarify_gateway.mark_awaiting_text") as mock_mark:
            await adapter._handle_clarify_other_action(ack, body, action)

        ack.assert_called_once()
        mock_mark.assert_called_once_with("cl-300")
        # Message updated to show free-text mode
        mock_client.chat_update.assert_called_once()
        update_kwargs = mock_client.chat_update.call_args[1]
        assert "free-text" in update_kwargs["text"] or "type your answer" in update_kwargs["text"]


# ===========================================================================
# _handle_thinking_toggle_action
# ===========================================================================


class TestThinkingToggleAction:
    @pytest.mark.asyncio
    async def test_collapse(self):
        adapter = _make_adapter()
        adapter._thinking_state["sess-1"] = "I was thinking deeply..."

        ack = AsyncMock()
        body = {
            "message": {
                "ts": "10.20",
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "Response text"}},
                    {"type": "actions", "block_id": "hermes_thinking_sess-1", "elements": []},
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": "🤖 model"}]},
                ],
            },
            "channel": {"id": "C1"},
            "user": {"name": "alice", "id": "U_ALICE"},
        }
        action = {"action_id": "hermes_thinking_toggle", "value": "sess-1:off"}

        mock_client = adapter._team_clients["T1"]
        mock_client.chat_update = AsyncMock()

        await adapter._handle_thinking_toggle_action(ack, body, action)

        ack.assert_called_once()
        mock_client.chat_update.assert_called_once()
        update_kwargs = mock_client.chat_update.call_args[1]
        # Thinking blocks replaced with collapsed toggle
        updated_blocks = update_kwargs["blocks"]
        thinking_blocks = [b for b in updated_blocks if b.get("block_id", "").startswith("hermes_thinking_")]
        assert len(thinking_blocks) == 1
        # Footer preserved
        context_blocks = [b for b in updated_blocks if b["type"] == "context"]
        assert len(context_blocks) == 1

    @pytest.mark.asyncio
    async def test_reveal(self):
        adapter = _make_adapter()
        adapter._thinking_state["sess-2"] = "My reasoning process..."

        ack = AsyncMock()
        body = {
            "message": {
                "ts": "10.20",
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "Response"}},
                    {"type": "actions", "block_id": "hermes_thinking_sess-2", "elements": []},
                ],
            },
            "channel": {"id": "C1"},
            "user": {"name": "bob", "id": "U_BOB"},
        }
        action = {"action_id": "hermes_thinking_toggle", "value": "sess-2:on"}

        mock_client = adapter._team_clients["T1"]
        mock_client.chat_update = AsyncMock()

        await adapter._handle_thinking_toggle_action(ack, body, action)

        update_kwargs = mock_client.chat_update.call_args[1]
        updated_blocks = update_kwargs["blocks"]
        # Should contain thinking content
        section_texts = [b["text"]["text"] for b in updated_blocks if b["type"] == "section"]
        combined = " ".join(section_texts)
        assert "My reasoning process" in combined

    @pytest.mark.asyncio
    async def test_no_thinking_content_stored(self):
        adapter = _make_adapter()
        # No thinking content for this session
        ack = AsyncMock()
        body = {
            "message": {"ts": "1.0", "blocks": []},
            "channel": {"id": "C1"},
            "user": {"name": "carol", "id": "U_CAROL"},
        }
        action = {"action_id": "hermes_thinking_toggle", "value": "no-such-sess:on"}

        mock_client = adapter._team_clients["T1"]
        mock_client.chat_update = AsyncMock()

        await adapter._handle_thinking_toggle_action(ack, body, action)

        # Should not update the message — no content to show
        mock_client.chat_update.assert_not_called()


# ===========================================================================
# _handle_model_select_action
# ===========================================================================


class TestModelSelectAction:
    @pytest.mark.asyncio
    async def test_posts_ephemeral(self):
        adapter = _make_adapter()
        ack = AsyncMock()
        body = {
            "message": {"ts": "1.0", "blocks": [
                {"type": "actions", "block_id": "hermes_model_sess-m", "elements": []},
            ]},
            "channel": {"id": "C1"},
            "user": {"name": "alice", "id": "U_ALICE"},
        }
        action = {
            "action_id": "hermes_model_select",
            "selected_option": {"value": "anthropic/claude-sonnet-4"},
        }

        mock_client = adapter._team_clients["T1"]
        mock_client.chat_postEphemeral = AsyncMock()

        await adapter._handle_model_select_action(ack, body, action)

        ack.assert_called_once()
        mock_client.chat_postEphemeral.assert_called_once()
        eph_kwargs = mock_client.chat_postEphemeral.call_args[1]
        assert "claude-sonnet-4" in eph_kwargs["text"]

    @pytest.mark.asyncio
    async def test_empty_value_ignored(self):
        adapter = _make_adapter()
        ack = AsyncMock()
        body = {
            "message": {"ts": "1.0", "blocks": []},
            "channel": {"id": "C1"},
            "user": {"name": "bob", "id": "U_BOB"},
        }
        action = {"action_id": "hermes_model_select", "selected_option": {"value": ""}}

        mock_client = adapter._team_clients["T1"]
        mock_client.chat_postEphemeral = AsyncMock()

        await adapter._handle_model_select_action(ack, body, action)

        mock_client.chat_postEphemeral.assert_not_called()


# ===========================================================================
# _handle_reasoning_select_action
# ===========================================================================


class TestReasoningSelectAction:
    @pytest.mark.asyncio
    async def test_posts_ephemeral(self):
        adapter = _make_adapter()
        ack = AsyncMock()
        body = {
            "message": {"ts": "1.0", "blocks": [
                {"type": "actions", "block_id": "hermes_reasoning_sess-r", "elements": []},
            ]},
            "channel": {"id": "C1"},
            "user": {"name": "alice", "id": "U_ALICE"},
        }
        action = {
            "action_id": "hermes_reasoning_select",
            "selected_option": {"value": "reasoning:high"},
        }

        mock_client = adapter._team_clients["T1"]
        mock_client.chat_postEphemeral = AsyncMock()

        await adapter._handle_reasoning_select_action(ack, body, action)

        ack.assert_called_once()
        mock_client.chat_postEphemeral.assert_called_once()
        eph_kwargs = mock_client.chat_postEphemeral.call_args[1]
        assert "high" in eph_kwargs["text"].lower() or "High" in eph_kwargs["text"]

    @pytest.mark.asyncio
    async def test_invalid_prefix_ignored(self):
        adapter = _make_adapter()
        ack = AsyncMock()
        body = {
            "message": {"ts": "1.0", "blocks": []},
            "channel": {"id": "C1"},
            "user": {"name": "bob", "id": "U_BOB"},
        }
        action = {
            "action_id": "hermes_reasoning_select",
            "selected_option": {"value": "not-reasoning:high"},
        }

        mock_client = adapter._team_clients["T1"]
        mock_client.chat_postEphemeral = AsyncMock()

        await adapter._handle_reasoning_select_action(ack, body, action)

        mock_client.chat_postEphemeral.assert_not_called()


# ===========================================================================
# store_thinking_content
# ===========================================================================


class TestStoreThinkingContent:
    def test_stores_for_chat(self):
        adapter = _make_adapter()
        adapter.store_thinking_content("C1", "I thought about this...", "sess-1")
        assert adapter._pending_thinking["C1"] == "I thought about this..."
        assert adapter._thinking_state["sess-1"] == "I thought about this..."

    def test_stores_without_session_key(self):
        adapter = _make_adapter()
        adapter.store_thinking_content("C1", "Some thinking")
        assert adapter._pending_thinking["C1"] == "Some thinking"

    def test_empty_text_ignored(self):
        adapter = _make_adapter()
        adapter.store_thinking_content("C1", "   ", "sess")
        assert "C1" not in adapter._pending_thinking

    def test_truncation(self):
        adapter = _make_adapter()
        long_text = "x" * 10000
        adapter.store_thinking_content("C1", long_text, "sess")
        assert len(adapter._pending_thinking["C1"]) <= 4000
