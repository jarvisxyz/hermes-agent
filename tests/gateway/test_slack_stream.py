"""Tests for SlackStreamConsumer — native Steps API streaming."""

import asyncio
import importlib.util
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.platforms.slack_stream import (
    SlackStreamConsumer,
    SlackStreamConfig,
    _DONE,
    _DELTA,
    _TASK_START,
    _TASK_UPDATE,
)


@pytest.fixture
def mock_client():
    """Create a mock AsyncWebClient with streaming methods."""
    client = MagicMock()
    client.chat_startStream = AsyncMock(return_value={"ts": "1234567890.123456"})
    client.chat_appendStream = AsyncMock(return_value={"ok": True})
    client.chat_stopStream = AsyncMock(return_value={"ok": True})
    return client


@pytest.fixture
def consumer(mock_client):
    """Create a SlackStreamConsumer instance for testing."""
    return SlackStreamConsumer(
        client=mock_client,
        channel_id="C0TEST",
        thread_ts="1234567890.000001",
        config=SlackStreamConfig(flush_interval=0.01, buffer_threshold=50),
    )


class TestSlackStreamConsumerInit:
    def test_default_config(self, mock_client):
        c = SlackStreamConsumer(mock_client, "C0", "123.456")
        assert c._channel_id == "C0"
        assert c._thread_ts == "123.456"
        assert c._stream_ts is None
        assert c._started is False
        assert c._response_step_id is None

    def test_custom_config(self, mock_client):
        cfg = SlackStreamConfig(show_thinking=False, feedback_buttons=False)
        c = SlackStreamConsumer(mock_client, "C0", "123.456", config=cfg)
        assert c._config.show_thinking is False
        assert c._config.feedback_buttons is False


class TestOnDelta:
    def test_queues_text(self, consumer):
        consumer.on_delta("Hello ")
        consumer.on_delta("world!")
        assert not consumer._queue.empty()
        items = []
        while not consumer._queue.empty():
            items.append(consumer._queue.get_nowait())
        assert len(items) == 2
        assert items[0] == (_DELTA, "Hello ")
        assert items[1] == (_DELTA, "world!")

    def test_ignores_empty(self, consumer):
        consumer.on_delta("")
        assert consumer._queue.empty()


class TestOnToolProgress:
    def test_tool_started(self, consumer):
        consumer.on_tool_progress("tool.started", tool_name="web_search", preview="query")
        item = consumer._queue.get_nowait()
        assert item[0] is _TASK_START
        assert item[1] == "tool_1"
        # Title is now human-readable with preview
        assert item[2] == "Web search: query"
        assert item[3] == "in_progress"
        # Description is built from args — empty here since no args dict provided
        assert item[4] == ""

    def test_tool_started_with_args(self, consumer):
        consumer.on_tool_progress("tool.started", tool_name="terminal", args={"command": "ls -la"})
        item = consumer._queue.get_nowait()
        assert item[0] is _TASK_START
        # Title includes the formatted tool name + preview from args
        assert "Terminal" in item[2]
        assert "command" in item[4]

    def test_tool_started_with_preview_and_args(self, consumer):
        """When both preview and args are given, desc still gets args (not just title)."""
        consumer.on_tool_progress(
            "tool.started", tool_name="terminal", preview="ls -la",
            args={"command": "ls -la"},
        )
        item = consumer._queue.get_nowait()
        assert item[0] is _TASK_START
        # Title uses preview
        assert item[2] == "Terminal: ls -la"
        # Desc is built from args even though preview exists
        assert "command" in item[4]
        assert "ls -la" in item[4]

    def test_tool_completed(self, consumer):
        # Start then complete
        consumer.on_tool_progress("tool.started", tool_name="web_search")
        consumer._queue.get_nowait()  # consume the start event
        consumer.on_tool_progress("tool.completed", tool_name="web_search", duration=2.5)
        item = consumer._queue.get_nowait()
        assert item[0] is _TASK_UPDATE
        assert item[3] == "complete"
        # Description shows just the duration — no redundant "Done"
        assert "Done" not in item[4]
        assert "2.5s" in item[4]

    def test_ignores_unknown_event(self, consumer):
        consumer.on_tool_progress("tool.unknown", tool_name="foo")
        assert consumer._queue.empty()

    def test_ignores_started_without_name(self, consumer):
        consumer.on_tool_progress("tool.started")
        assert consumer._queue.empty()


class TestOnThinking:
    def test_thinking_start(self, consumer):
        consumer.on_thinking_start()
        item = consumer._queue.get_nowait()
        assert item[0] is _TASK_START
        assert item[2] == "Thinking"
        assert item[3] == "in_progress"

    def test_thinking_end(self, consumer):
        consumer.on_thinking_start()
        consumer._queue.get_nowait()  # consume start
        consumer.on_thinking_end()
        item = consumer._queue.get_nowait()
        assert item[0] is _TASK_UPDATE
        assert item[2] == "Thinking"
        assert item[3] == "complete"

    def test_thinking_disabled(self, mock_client):
        cfg = SlackStreamConfig(show_thinking=False)
        c = SlackStreamConsumer(mock_client, "C0", "123.456", config=cfg)
        c.on_thinking_start()
        assert c._queue.empty()


class TestFinish:
    def test_queues_done(self, consumer):
        consumer.finish()
        item = consumer._queue.get_nowait()
        assert item is _DONE


class TestRunLifecycle:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self, consumer, mock_client):
        """Test the full stream lifecycle: start → thinking → tools → text → stop."""
        # Start the run in background
        run_task = asyncio.create_task(consumer.run())
        # Give it a moment to call startStream
        await asyncio.sleep(0.05)

        # Simulate agent activity
        consumer.on_thinking_start()
        await asyncio.sleep(0.05)
        consumer.on_thinking_end()
        await asyncio.sleep(0.05)
        consumer.on_tool_progress("tool.started", tool_name="web_search", preview="test query")
        await asyncio.sleep(0.05)
        consumer.on_tool_progress("tool.completed", tool_name="web_search", duration=1.0)
        await asyncio.sleep(0.05)
        consumer.on_delta("Hello! Here are the results.")
        await asyncio.sleep(0.15)
        consumer.finish()

        # Wait for run to complete
        await asyncio.wait_for(run_task, timeout=5.0)

        # Verify startStream was called with plan mode
        mock_client.chat_startStream.assert_called_once_with(
            channel="C0TEST",
            thread_ts="1234567890.000001",
            task_display_mode="plan",
        )

        # Verify stopStream was called
        mock_client.chat_stopStream.assert_called_once_with(
            channel="C0TEST",
            ts="1234567890.123456",
        )

        # Verify appendStream was called for chunks (task steps + response step)
        assert mock_client.chat_appendStream.call_count >= 3  # thinking + tool + response

    @pytest.mark.asyncio
    async def test_start_stream_failure(self, mock_client):
        """Graceful handling when startStream fails."""
        mock_client.chat_startStream.side_effect = Exception("API error")
        c = SlackStreamConsumer(mock_client, "C0", "123.456")
        await c.run()
        # Should not crash — just return
        mock_client.chat_stopStream.assert_not_called()

    @pytest.mark.asyncio
    async def test_append_stream_failure(self, consumer, mock_client):
        """Graceful handling when appendStream fails."""
        mock_client.chat_appendStream.side_effect = Exception("rate limited")
        run_task = asyncio.create_task(consumer.run())
        await asyncio.sleep(0.05)
        consumer.on_delta("Some text")
        await asyncio.sleep(0.1)
        consumer.finish()
        await asyncio.wait_for(run_task, timeout=5.0)

    @pytest.mark.asyncio
    async def test_text_creates_response_step(self, consumer, mock_client):
        """Text deltas create and update a Response step in plan mode."""
        run_task = asyncio.create_task(consumer.run())
        await asyncio.sleep(0.05)
        consumer.on_delta("Hello world!")
        await asyncio.sleep(0.15)
        consumer.finish()
        await asyncio.wait_for(run_task, timeout=5.0)

        # Verify that at least one appendStream call used chunks (not markdown_text)
        chunk_calls = [
            call for call in mock_client.chat_appendStream.call_args_list
            if call.kwargs.get("chunks")
        ]
        assert len(chunk_calls) >= 1
        # No call should use markdown_text (plan mode forbids it)
        md_calls = [
            call for call in mock_client.chat_appendStream.call_args_list
            if call.kwargs.get("markdown_text")
        ]
        assert len(md_calls) == 0


class TestFormatToolTitle:
    def test_known_tool(self):
        assert SlackStreamConsumer._format_tool_title("search_files") == "Search files"

    def test_known_tool_with_preview(self):
        assert SlackStreamConsumer._format_tool_title("terminal", "ls -la") == "Terminal: ls -la"

    def test_unknown_tool(self):
        assert SlackStreamConsumer._format_tool_title("my_custom_tool") == "My Custom Tool"

    def test_long_preview_truncated(self):
        title = SlackStreamConsumer._format_tool_title("terminal", "x" * 100)
        assert len(title) <= len("Terminal: ") + 60 + 1  # +1 for ellipsis
        assert title.endswith("…")

    def test_empty_preview(self):
        assert SlackStreamConsumer._format_tool_title("web_search", "") == "Web search"


class TestInitialStep:
    @pytest.mark.asyncio
    async def test_initial_step_emitted_on_start(self, consumer, mock_client):
        """startStream immediately emits a 'Processing' step."""
        run_task = asyncio.create_task(consumer.run())
        await asyncio.sleep(0.05)
        # The initial step should have been sent
        append_calls = mock_client.chat_appendStream.call_args_list
        # At least one call should exist for the initial step
        assert len(append_calls) >= 1
        consumer.finish()
        await asyncio.wait_for(run_task, timeout=5.0)

    @pytest.mark.asyncio
    async def test_initial_step_completed_on_first_tool(self, consumer, mock_client):
        """Initial step is completed when the first tool starts."""
        run_task = asyncio.create_task(consumer.run())
        await asyncio.sleep(0.05)
        consumer.on_tool_progress("tool.started", tool_name="web_search", preview="test")
        await asyncio.sleep(0.05)
        consumer.finish()
        await asyncio.wait_for(run_task, timeout=5.0)
        # The initial step should have been completed (not left hanging)
        assert consumer._initial_step_id is None

    @pytest.mark.asyncio
    async def test_initial_step_completed_on_first_delta(self, consumer, mock_client):
        """Initial step is completed when the first text delta arrives."""
        run_task = asyncio.create_task(consumer.run())
        await asyncio.sleep(0.05)
        consumer.on_delta("Hello!")
        await asyncio.sleep(0.15)
        consumer.finish()
        await asyncio.wait_for(run_task, timeout=5.0)
        assert consumer._initial_step_id is None


class TestTaskIdTracking:
    def test_incrementing_task_ids(self, consumer):
        consumer.on_tool_progress("tool.started", tool_name="tool_a")
        consumer.on_tool_progress("tool.started", tool_name="tool_b")
        item_a = consumer._queue.get_nowait()
        item_b = consumer._queue.get_nowait()
        # IDs are sequential (counter starts at 0, first on_tool_progress increments)
        assert item_a[1] == "tool_1"
        assert item_b[1] == "tool_2"

    def test_complete_matches_start(self, consumer):
        consumer.on_tool_progress("tool.started", tool_name="web_search")
        start_item = consumer._queue.get_nowait()
        task_id = start_item[1]
        consumer.on_tool_progress("tool.completed", tool_name="web_search")
        update_item = consumer._queue.get_nowait()
        assert update_item[1] == task_id

    def test_completed_preserves_description(self, consumer):
        """Completed tool step preserves in-progress description + shows duration."""
        consumer.on_tool_progress(
            "tool.started", tool_name="terminal", args={"command": "ls -la"},
        )
        consumer._queue.get_nowait()  # consume start
        consumer.on_tool_progress(
            "tool.completed", tool_name="terminal", duration=1.2,
        )
        item = consumer._queue.get_nowait()
        # Description should contain the original args and duration — no "Done"
        assert "command" in item[4]
        assert "1.2s" in item[4]
        assert "Done" not in item[4]
        # The description should have a newline separating args from duration
        assert "\n" in item[4]

    def test_completed_preserves_title_with_args(self, consumer):
        """Completed step title includes the args preview, not just tool name."""
        consumer.on_tool_progress(
            "tool.started", tool_name="terminal", args={"command": "gh pr view 9"},
        )
        consumer._queue.get_nowait()  # consume start
        consumer.on_tool_progress(
            "tool.completed", tool_name="terminal", duration=0.8,
        )
        item = consumer._queue.get_nowait()
        # Title should preserve the command, not revert to just "Terminal"
        assert "gh pr view 9" in item[2]


class TestMakeTaskChunk:
    @pytest.mark.skipif(
        not importlib.util.find_spec("slack_sdk"),
        reason="slack_sdk not installed"
    )
    def test_typed_chunk_with_sdk(self, consumer):
        """When slack_sdk is available, _make_task_chunk returns TaskUpdateChunk."""
        chunk = consumer._make_task_chunk("t1", "Web Search", "in_progress", details="query: test")
        # Should be a TaskUpdateChunk if SDK is available
        from slack_sdk.models.messages.chunk import TaskUpdateChunk
        assert isinstance(chunk, TaskUpdateChunk)

    @pytest.mark.skipif(
        not importlib.util.find_spec("slack_sdk"),
        reason="slack_sdk not installed"
    )
    def test_chunk_with_output(self, consumer):
        """TaskUpdateChunk with output field for Response step text."""
        chunk = consumer._make_task_chunk("r1", "Response", "complete", output="Hello world")
        from slack_sdk.models.messages.chunk import TaskUpdateChunk
        assert isinstance(chunk, TaskUpdateChunk)
        # output field should be set
        assert chunk.output == "Hello world"


class TestFlushText:
    @pytest.mark.asyncio
    async def test_buffer_threshold_flush(self, mock_client):
        """Text is flushed to Response step when buffer exceeds threshold."""
        cfg = SlackStreamConfig(flush_interval=100, buffer_threshold=20)
        c = SlackStreamConsumer(mock_client, "C0", "123.456", config=cfg)
        # Start the run loop, which calls _start_stream internally
        run_task = asyncio.create_task(c.run())
        await asyncio.sleep(0.05)
        c.on_delta("A" * 30)  # exceeds threshold of 20
        await asyncio.sleep(0.15)
        c.finish()
        await asyncio.wait_for(run_task, timeout=5.0)
        # Verify appendStream was called with chunks (not markdown_text)
        chunk_calls = [
            call for call in mock_client.chat_appendStream.call_args_list
            if call.kwargs.get("chunks")
        ]
        assert len(chunk_calls) >= 1
        # Verify no markdown_text calls (plan mode)
        md_calls = [
            call for call in mock_client.chat_appendStream.call_args_list
            if call.kwargs.get("markdown_text")
        ]
        assert len(md_calls) == 0


class TestSlackStreamConfig:
    def test_defaults(self):
        cfg = SlackStreamConfig()
        assert cfg.flush_interval == 1.0
        assert cfg.buffer_threshold == 1200
        assert cfg.show_thinking is True
        assert cfg.set_title is True
        assert cfg.feedback_buttons is True
        assert cfg.response_step_title == "Response"
