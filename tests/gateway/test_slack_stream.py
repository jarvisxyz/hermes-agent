"""Tests for SlackStreamConsumer — native Steps API streaming."""

import asyncio
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
        assert item[2] == "web_search"
        assert item[3] == "in_progress"
        assert item[4] == "query"

    def test_tool_started_with_args(self, consumer):
        consumer.on_tool_progress("tool.started", tool_name="terminal", args={"command": "ls -la"})
        item = consumer._queue.get_nowait()
        assert item[0] is _TASK_START
        assert item[2] == "terminal"
        assert "command" in item[4]

    def test_tool_completed(self, consumer):
        # Start then complete
        consumer.on_tool_progress("tool.started", tool_name="web_search")
        consumer._queue.get_nowait()  # consume the start event
        consumer.on_tool_progress("tool.completed", tool_name="web_search", duration=2.5)
        item = consumer._queue.get_nowait()
        assert item[0] is _TASK_UPDATE
        assert item[2] == "web_search"
        assert item[3] == "complete"
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
        """Test the full stream lifecycle: start → deltas → tasks → stop."""
        # Start the run in background
        run_task = asyncio.create_task(consumer.run())
        # Give it a moment to call startStream
        await asyncio.sleep(0.05)

        # Simulate agent activity
        consumer.on_delta("Hello ")
        consumer.on_delta("world!")
        await asyncio.sleep(0.05)
        consumer.on_tool_progress("tool.started", tool_name="web_search", preview="test query")
        await asyncio.sleep(0.05)
        consumer.on_tool_progress("tool.completed", tool_name="web_search", duration=1.0)
        await asyncio.sleep(0.05)
        consumer.on_delta(" Here are the results.")
        await asyncio.sleep(0.1)
        consumer.finish()

        # Wait for run to complete
        await asyncio.wait_for(run_task, timeout=5.0)

        # Verify startStream was called
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

        # Verify appendStream was called for text and chunks
        assert mock_client.chat_appendStream.call_count >= 2  # at least text + task_start

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


class TestTaskIdTracking:
    def test_incrementing_task_ids(self, consumer):
        consumer.on_tool_progress("tool.started", tool_name="tool_a")
        consumer.on_tool_progress("tool.started", tool_name="tool_b")
        item_a = consumer._queue.get_nowait()
        item_b = consumer._queue.get_nowait()
        assert item_a[1] == "tool_1"
        assert item_b[1] == "tool_2"

    def test_complete_matches_start(self, consumer):
        consumer.on_tool_progress("tool.started", tool_name="web_search")
        start_item = consumer._queue.get_nowait()
        task_id = start_item[1]
        consumer.on_tool_progress("tool.completed", tool_name="web_search")
        update_item = consumer._queue.get_nowait()
        assert update_item[1] == task_id


class TestFlushText:
    @pytest.mark.asyncio
    async def test_buffer_threshold_flush(self, mock_client):
        """Text is flushed when buffer exceeds threshold."""
        cfg = SlackStreamConfig(flush_interval=100, buffer_threshold=20)
        c = SlackStreamConsumer(mock_client, "C0", "123.456", config=cfg)
        # Simulate the run loop manually
        await c._start_stream()
        c.on_delta("A" * 30)  # exceeds threshold of 20
        # Let the run loop process it
        c.finish()
        await c.run()
        # Verify appendStream was called with the text
        text_calls = [
            call for call in mock_client.chat_appendStream.call_args_list
            if call.kwargs.get("markdown_text")
        ]
        assert len(text_calls) >= 1
        assert "A" * 30 in text_calls[0].kwargs["markdown_text"]


class TestSlackStreamConfig:
    def test_defaults(self):
        cfg = SlackStreamConfig()
        assert cfg.flush_interval == 0.8
        assert cfg.buffer_threshold == 800
        assert cfg.show_thinking is True
        assert cfg.set_title is True
        assert cfg.feedback_buttons is True
