"""Slack AI Assistant streaming consumer — native step indicators via the Steps API.

Uses Slack's ``chat.startStream`` / ``chat.appendStream`` / ``chat.stopStream``
methods with ``TaskUpdateChunk`` objects to render native collapsible step cards
with checkmarks, chevrons, and status indicators — the same UI pattern used
by Slack's own AI assistants (e.g. Slack AI, Highbeam's Luma).

Architecture
------------
Instead of the legacy postMessage → chat.update edit loop, this consumer:

1. Calls ``chat.startStream`` with ``task_display_mode="plan"`` to create a
   streaming message container in **plan mode**.  The response includes a
   ``ts`` for the stream.

2. Calls ``chat.appendStream`` with ``TaskUpdateChunk`` objects for each step:
   - When a tool starts:  ``TaskUpdateChunk(id, title, status="in_progress")``
   - When a tool completes: ``TaskUpdateChunk(id, title, status="complete", details=...)``
   - When a tool fails:  ``TaskUpdateChunk(id, title, status="failed", details=...)``

3. For the model's text output, emits a **"Response" task step** whose
   ``output`` field carries the markdown text.  This is required because
   plan mode streams cannot use ``markdown_text`` — they only accept
   ``chunks``.  The text is buffered and flushed periodically as incremental
   updates to the Response step's ``output``.

4. Calls ``chat.stopStream`` to finalize.

.. important::
   Slack's streaming API enforces **mode isolation**: a stream started with
   ``task_display_mode="plan"`` can only accept ``chunks`` (not
   ``markdown_text``), and vice versa.  Attempting to mix them raises
   ``streaming_mode_mismatch``.  This consumer uses plan mode exclusively
   and routes all text through ``TaskUpdateChunk.output``.

Thread Safety
-------------
Like the base ``GatewayStreamConsumer``, all public ``on_*`` methods are
synchronous and thread-safe (called from the agent's worker thread).  The
actual Slack API calls happen in an async ``run()`` task on the event loop.

Requirements
------------
- Slack app must have the ``assistant:write`` scope.
- The ``Agents & AI Apps`` feature must be enabled in the Slack app config.
- The conversation must be in a Slack thread (``thread_ts`` is required for
  ``chat.startStream``).
- ``slack_sdk >= 3.35`` (for streaming method support).
"""

from __future__ import annotations

import asyncio
import logging
import queue
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("gateway.slack_stream")

# ── Queue sentinels ──────────────────────────────────────────────────────

_DONE = object()        # Stream is complete
_DELTA = object()       # Text delta from the model
_TASK_START = object()  # Tool started
_TASK_UPDATE = object() # Tool completed / failed
_THINKING = object()    # Thinking/reasoning started or ended


@dataclass
class StreamChunk:
    """Internal representation of a queued chunk."""
    kind: str           # "delta" | "task_start" | "task_update" | "thinking" | "done"
    task_id: str = ""
    task_name: str = ""
    status: str = ""    # "in_progress" | "complete" | "failed"
    description: str = ""
    text: str = ""


@dataclass
class SlackStreamConfig:
    """Runtime config for a Slack streaming consumer instance."""
    # How often to flush accumulated text to the Response step (seconds)
    flush_interval: float = 1.0
    # Maximum characters to buffer before flushing regardless of interval
    buffer_threshold: int = 1200
    # Whether to include thinking/reasoning as a task step
    show_thinking: bool = True
    # Whether to set thread title on first response
    set_title: bool = True
    # Whether to include feedback buttons on the final message
    feedback_buttons: bool = True
    # Title for the Response step that carries the LLM text output
    response_step_title: str = "Response"


class SlackStreamConsumer:
    """Async consumer that streams AI responses via Slack's native Steps API.

    Usage::

        consumer = SlackStreamConsumer(
            client=slack_client,
            channel_id="C01234",
            thread_ts="1234567890.123456",
            config=SlackStreamConfig(),
        )
        # Pass consumer callbacks to AIAgent
        agent = AIAgent(
            ...,
            stream_delta_callback=consumer.on_delta,
            tool_progress_callback=consumer.on_tool_progress,
        )
        # Start the consumer
        task = asyncio.create_task(consumer.run())
        # ... run agent ...
        consumer.finish()
        await task
    """

    def __init__(
        self,
        client: Any,              # AsyncWebClient
        channel_id: str,
        thread_ts: str,
        config: Optional[SlackStreamConfig] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self._client = client
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._config = config or SlackStreamConfig()
        self._metadata = metadata or {}

        # Thread-safe queue — on_delta / on_tool_progress are called from
        # the agent's worker thread; run() drains from the async loop.
        self._queue: queue.Queue = queue.Queue()

        # Stream state — set by run() after startStream succeeds
        self._stream_ts: Optional[str] = None

        # Running counter for task IDs
        self._task_counter: int = 0

        # Buffer for text deltas — accumulated and flushed to the Response
        # step's ``output`` field periodically
        self._text_buffer: str = ""

        # Track active tasks so we can complete/fail them
        self._active_tasks: Dict[str, str] = {}  # task_id → name
        self._active_task_descs: Dict[str, str] = {}  # task_id → in-progress description
        self._active_task_previews: Dict[str, str] = {}  # task_id → original preview string

        # Whether we've started the Response step
        self._response_step_id: Optional[str] = None

        # ID of the initial "Processing" step emitted at stream start
        # to replace Slack's "Gathering information…" placeholder
        self._initial_step_id: Optional[str] = None

        # Total text sent so far (for incremental output updates)
        self._total_text_sent: int = 0

        # Whether we've sent the first appendStream
        self._started: bool = False

        # Set True when an appendStream call fails with
        # message_not_in_streaming_state — prevents further API spam
        self._stream_broken: bool = False

    # ── Public sync callbacks (called from agent worker thread) ───────

    # Friendly display names for tool step titles.  Tools not listed here
    # fall back to title-case conversion of the snake_case name.
    _TOOL_DISPLAY_NAMES: Dict[str, str] = {
        "terminal": "Terminal",
        "web_search": "Web search",
        "web_extract": "Web extract",
        "read_file": "Read file",
        "write_file": "Write file",
        "patch": "Patch",
        "search_files": "Search files",
        "browser_navigate": "Navigate",
        "browser_click": "Click",
        "browser_type": "Type",
        "browser_snapshot": "Snapshot",
        "browser_vision": "Vision",
        "browser_scroll": "Scroll",
        "browser_press": "Press key",
        "browser_back": "Back",
        "browser_get_images": "Get images",
        "browser_console": "Console",
        "image_generate": "Image generate",
        "text_to_speech": "Text to speech",
        "vision_analyze": "Vision analyze",
        "execute_code": "Execute code",
        "delegate_task": "Delegate task",
        "clarify": "Clarify",
        "skill_view": "Skill view",
        "skills_list": "Skills list",
        "skill_manage": "Skill manage",
        "memory": "Memory",
        "cronjob": "Cron job",
        "todo": "Todo",
        "process": "Process",
        "send_message": "Send message",
        "session_search": "Session search",
    }

    @classmethod
    def _format_tool_title(cls, tool_name: str, preview: str = "") -> str:
        """Return a human-readable step title for a tool.

        If a preview string is available (the primary argument), it's
        appended after a colon for immediate visibility in the step card.
        """
        display = cls._TOOL_DISPLAY_NAMES.get(tool_name)
        if display is None:
            # Fallback: convert snake_case to Title Case
            display = tool_name.replace("_", " ").title()
        if preview:
            # Truncate long previews for the title
            short = preview[:60] + ("…" if len(preview) > 60 else "")
            return f"{display}: {short}"
        return display

    def on_delta(self, text: str) -> None:
        """Thread-safe callback — called from the agent's worker thread."""
        if text:
            self._queue.put((_DELTA, text))

    def on_tool_progress(
        self,
        event_type: str,
        tool_name: str = None,
        preview: str = None,
        args: dict = None,
        **kwargs,
    ) -> None:
        """Thread-safe callback for tool lifecycle events.

        Maps agent tool events to Slack TaskUpdateChunk steps:
        - ``tool.started``  → in_progress step
        - ``tool.completed`` → complete step
        """
        if event_type == "tool.started" and tool_name:
            self._task_counter += 1
            task_id = f"tool_{self._task_counter}"
            # Build the description from args — always, even when preview exists.
            # Preview goes in the title; desc (subtitle) shows args for context.
            desc = ""
            if args:
                first_key = next(iter(args), None)
                if first_key:
                    val = str(args[first_key])[:80]
                    desc = f"{first_key}: {val}"
            # Format a human-readable title with preview (e.g. "Search files: *.py")
            title = self._format_tool_title(tool_name, preview or desc)
            self._queue.put((_TASK_START, task_id, title, "in_progress", desc))
            self._active_tasks[task_id] = tool_name
            self._active_task_descs[task_id] = desc
            self._active_task_previews[task_id] = preview or ""

        elif event_type == "tool.completed" and tool_name:
            # Find the matching active task
            task_id = None
            for tid, name in list(self._active_tasks.items()):
                if name == tool_name:
                    task_id = tid
                    break
            if task_id:
                duration = kwargs.get("duration", 0)
                # Just show the duration — checkmark already indicates completion.
                # Slack APPENDS the details field across updates, so only send
                # the duration here; the in-progress description is already visible.
                dur_str = f" ({duration:.1f}s)" if duration else ""
                desc = dur_str
                # Preserve the original title (with preview) on completion too
                original_preview = self._active_task_previews.get(task_id, "")
                title = self._format_tool_title(tool_name, original_preview)
                self._queue.put((_TASK_UPDATE, task_id, title, "complete", desc))
                self._active_tasks.pop(task_id, None)
                self._active_task_descs.pop(task_id, None)
                self._active_task_previews.pop(task_id, None)

    def on_thinking_start(self) -> None:
        """Signal that the model is thinking/reasoning."""
        if self._config.show_thinking:
            self._task_counter += 1
            task_id = f"thinking_{self._task_counter}"
            self._queue.put((_TASK_START, task_id, "Thinking", "in_progress", ""))
            self._active_tasks[task_id] = "Thinking"

    def on_thinking_end(self) -> None:
        """Signal that thinking/reasoning is complete."""
        for task_id, name in list(self._active_tasks.items()):
            if name == "Thinking":
                self._queue.put((_TASK_UPDATE, task_id, "Thinking", "complete", ""))
                self._active_tasks.pop(task_id, None)
                break

    def finish(self) -> None:
        """Signal that the stream is complete."""
        self._queue.put(_DONE)

    # ── Async run loop ───────────────────────────────────────────────

    async def run(self) -> None:
        """Main async loop — drains the queue and calls Slack streaming APIs."""
        try:
            await self._start_stream()
        except Exception as e:
            logger.error("[SlackStream] Failed to start stream: %s", e, exc_info=True)
            return

        last_flush = time.monotonic()

        while True:
            # Drain queue without blocking the event loop.
            # queue.Queue is thread-safe but blocking; use a short
            # loop + async sleep to stay responsive.
            item = None
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                pass

            if item is None:
                # Periodic flush of buffered text to the Response step
                now = time.monotonic()
                if self._text_buffer and (now - last_flush) >= self._config.flush_interval:
                    await self._flush_text()
                    last_flush = now
                await asyncio.sleep(0.05)
                continue

            if item is _DONE:
                # Finalize any remaining text and active tasks
                await self._finalize()
                break

            kind = item[0]

            if kind is _DELTA:
                await self._complete_initial_step()
                self._text_buffer += item[1]
                now = time.monotonic()
                if len(self._text_buffer) >= self._config.buffer_threshold or \
                   (now - last_flush) >= self._config.flush_interval:
                    await self._flush_text()
                    last_flush = now

            elif kind is _TASK_START:
                _, task_id, task_name, status, desc = item
                await self._complete_initial_step(replacement_title=task_name)
                await self._send_task_step(task_id, task_name, status, desc)

            elif kind is _TASK_UPDATE:
                _, task_id, task_name, status, desc = item
                await self._send_task_step(task_id, task_name, status, desc)

    # ── Slack API calls ──────────────────────────────────────────────

    async def _complete_initial_step(
        self, replacement_title: Optional[str] = None,
    ) -> None:
        """Complete the initial 'Processing' step once real content arrives.

        If *replacement_title* is given (e.g. the formatted name of the first
        tool), the initial step's title is updated to show it before being
        marked complete — this way the user sees the actual tool name flash
        briefly before the dedicated tool step card takes over.
        """
        if self._initial_step_id is None or self._stream_broken:
            return
        step_id = self._initial_step_id
        self._initial_step_id = None  # only complete once
        try:
            title = replacement_title or "Processing"
            await self._send_task_step(step_id, title, "complete", "")
            self._active_tasks.pop(step_id, None)
        except Exception as e:
            logger.debug("[SlackStream] complete_initial_step failed: %s", e)

    async def _start_stream(self) -> None:
        """Initiate a streaming message via chat.startStream in plan mode.

        Immediately emits an initial "Processing…" step so Slack's built-in
        "Gathering information…" placeholder is replaced as fast as possible.
        This step is completed once the first real tool step or text delta
        arrives.

        Raises on failure so the ``run()`` loop falls back to non-streaming.
        """
        resp = await self._client.chat_startStream(
            channel=self._channel_id,
            thread_ts=self._thread_ts,
            task_display_mode="plan",
        )
        # Validate the response — Slack returns HTTP 200 even on errors
        # with {ok: false, error: "..."}
        ok = resp.get("ok", True) if isinstance(resp, dict) else getattr(resp, "data", {}).get("ok", True)
        if ok is False:
            error = resp.get("error", "unknown_error") if isinstance(resp, dict) else "unknown_error"
            raise RuntimeError(f"chat.startStream failed: {error}")

        self._stream_ts = resp.get("ts") or resp.get("message", {}).get("ts")
        if not self._stream_ts:
            # Some SDK versions nest differently
            data = resp.data if hasattr(resp, "data") else resp
            self._stream_ts = data.get("ts") or data.get("message", {}).get("ts")
        if not self._stream_ts:
            raise RuntimeError("chat.startStream returned no message ts")
        logger.debug(
            "[SlackStream] Started stream: channel=%s thread=%s stream_ts=%s",
            self._channel_id, self._thread_ts, self._stream_ts,
        )
        self._started = True

        # Emit an initial step immediately to replace Slack's "Gathering
        # information…" placeholder.  This step will be completed (and
        # potentially replaced by the first real tool step) as soon as
        # the agent starts producing output.
        self._task_counter += 1
        self._initial_step_id = f"init_{self._task_counter}"
        try:
            await self._send_task_step(
                self._initial_step_id,
                "Processing",
                "in_progress",
                "",
            )
        except Exception as e:
            # If the very first appendStream fails, the stream is unusable —
            # raise so run() can bail out early instead of spamming failures.
            raise RuntimeError(f"Initial appendStream failed after startStream: {e}") from e
        self._active_tasks[self._initial_step_id] = "Processing"

    async def _ensure_response_step(self) -> str:
        """Create the Response step if it doesn't exist yet, return its ID."""
        if self._response_step_id is not None:
            return self._response_step_id
        if self._stream_broken:
            # Return a dummy ID — we can't actually create the step
            self._response_step_id = f"response_broken"
            return self._response_step_id
        self._task_counter += 1
        self._response_step_id = f"response_{self._task_counter}"
        # Start the Response step as in_progress
        await self._send_task_step(
            self._response_step_id,
            self._config.response_step_title,
            "in_progress",
            "",
        )
        return self._response_step_id

    async def _flush_text(self) -> None:
        """Flush accumulated text deltas to the Response step's output field.

        In plan mode, ``markdown_text`` is not allowed — text must be carried
        inside a ``TaskUpdateChunk.output`` field.  We maintain a "Response"
        step and update it with the new text each flush.

        Slack's ``chat.appendStream`` **appends** the ``output`` field across
        updates (same as ``details``), so we must send only the delta — the
        new text since the last flush — not the full buffer.
        """
        if not self._text_buffer or not self._stream_ts or self._stream_broken:
            return
        # Only send text we haven't already flushed
        new_text = self._text_buffer[self._total_text_sent:]
        if not new_text:
            return
        try:
            step_id = await self._ensure_response_step()
            chunk = self._make_task_chunk(
                step_id,
                self._config.response_step_title,
                "in_progress",
                output=new_text,
            )
            await self._client.chat_appendStream(
                channel=self._channel_id,
                ts=self._stream_ts,
                chunks=[chunk],
            )
            self._total_text_sent = len(self._text_buffer)
            # Don't clear the buffer — we need the full text to compute
            # the delta for the next flush.
        except Exception as e:
            if "message_not_in_streaming_state" in str(e):
                self._stream_broken = True
                logger.debug("[SlackStream] Stream closed during text flush — suppressing further calls")
            else:
                logger.warning("[SlackStream] flush text to Response step failed: %s", e)

    async def _send_task_step(self, task_id: str, name: str, status: str, description: str) -> None:
        """Send a TaskUpdateChunk for a tool/thinking step."""
        if not self._stream_ts or self._stream_broken:
            return
        try:
            chunk = self._make_task_chunk(task_id, name, status, details=description or None)
            await self._client.chat_appendStream(
                channel=self._channel_id,
                ts=self._stream_ts,
                chunks=[chunk],
            )
        except Exception as e:
            if "message_not_in_streaming_state" in str(e):
                self._stream_broken = True
                logger.debug("[SlackStream] Stream closed — suppressing further appendStream calls")
            else:
                logger.warning("[SlackStream] task step failed for %s: %s", name, e)

    def _make_task_chunk(
        self,
        task_id: str,
        title: str,
        status: str,
        details: Optional[str] = None,
        output: Optional[str] = None,
    ) -> Any:
        """Build a TaskUpdateChunk, falling back to a raw dict for old SDKs."""
        try:
            from slack_sdk.models.messages.chunk import TaskUpdateChunk
            kwargs: Dict[str, Any] = {
                "id": task_id,
                "title": title,
                "status": status,
            }
            if details:
                kwargs["details"] = details
            if output:
                kwargs["output"] = output
            return TaskUpdateChunk(**kwargs)
        except ImportError:
            # Fallback for slack_sdk < 3.35
            chunk: Dict[str, Any] = {
                "type": "task_update",
                "id": task_id,
                "name": title,
                "status": status,
            }
            if details:
                chunk["description"] = details
            if output:
                chunk["output"] = output
            return chunk

    async def _finalize(self) -> None:
        """Flush remaining text, complete active tasks, and stop the stream.

        Handles ``message_not_in_streaming_state`` gracefully — if Slack has
        already closed the stream (server-side timeout), we can't append any
        more chunks but the content already rendered is intact.
        """
        if self._stream_broken:
            # Stream is already closed server-side — just try stopStream
            # for cleanup and return.
            await self._try_stop_stream()
            return

        _STREAM_CLOSED = "message_not_in_streaming_state"

        # Complete any still-active tool/thinking tasks
        for task_id, name in list(self._active_tasks.items()):
            try:
                await self._send_task_step(task_id, name, "complete", "")
            except Exception as e:
                err_str = str(e)
                if _STREAM_CLOSED in err_str:
                    logger.debug("[SlackStream] Stream already closed, skipping task completion for %s", name)
                    break  # No point trying more tasks
                pass

        # Flush remaining text and mark the Response step as complete
        if self._text_buffer or self._response_step_id:
            try:
                step_id = await self._ensure_response_step()
                # Only send text not yet flushed (Slack appends output)
                new_text = (self._text_buffer or "")[self._total_text_sent:]
                chunk = self._make_task_chunk(
                    step_id,
                    self._config.response_step_title,
                    "complete",
                    output=new_text or None,
                )
                await self._client.chat_appendStream(
                    channel=self._channel_id,
                    ts=self._stream_ts,
                    chunks=[chunk],
                )
                self._text_buffer = ""
            except Exception as e:
                err_str = str(e)
                if _STREAM_CLOSED in err_str:
                    logger.debug(
                        "[SlackStream] Stream already closed before final text flush — "
                        "content already rendered."
                    )
                else:
                    logger.warning("[SlackStream] finalize Response step failed: %s", e)

        # Stop the stream
        await self._try_stop_stream()

        self._started = False

    async def _try_stop_stream(self) -> None:
        """Best-effort call to chat.stopStream — safe even if stream is already closed."""
        if not self._stream_ts:
            return
        try:
            await self._client.chat_stopStream(
                channel=self._channel_id,
                ts=self._stream_ts,
            )
            logger.debug(
                "[SlackStream] Stopped stream: stream_ts=%s",
                self._stream_ts,
            )
        except Exception as e:
            err_str = str(e)
            if "message_not_in_streaming_state" in err_str:
                logger.debug("[SlackStream] stopStream: stream already closed server-side")
            else:
                logger.warning("[SlackStream] stopStream failed: %s", e)
