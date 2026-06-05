"""Slack AI Assistant streaming consumer — native step indicators via the Steps API.

Uses Slack's ``chat.startStream`` / ``chat.appendStream`` / ``chat.stopStream``
methods with ``TaskUpdateChunk`` and ``MarkdownTextChunk`` objects to render
native collapsible step cards with checkmarks, chevrons, and status indicators
— the same UI pattern used by Slack's own AI assistants (e.g. Slack AI,
Highbeam's Luma).

Architecture
------------
Instead of the legacy postMessage → chat.update edit loop, this consumer:

1. Calls ``chat.startStream`` with a configurable ``task_display_mode``
   (default: ``"timeline"``) to create a streaming message container.
   The response includes a ``ts`` for the stream.

2. Calls ``chat.appendStream`` with ``TaskUpdateChunk`` objects for each step:
   - When a tool starts:  ``TaskUpdateChunk(id, title, status="in_progress")``
   - When a tool completes: ``TaskUpdateChunk(id, title, status="complete", details=...)``
   - When a tool fails:  ``TaskUpdateChunk(id, title, status="failed", details=...)``

3. For the model's text output:

   - **Timeline / dense mode** (default): Text is delivered via
     ``MarkdownTextChunk`` objects which render proper Slack mrkdwn
     (bold, italic, links, headers, code blocks, etc.) with a
     12 000-char limit per call.

   - **Plan mode** (legacy): ``markdown_text`` is not allowed — text
     must go through ``TaskUpdateChunk.output`` (256-char limit, no
     markdown rendering).  A "Response" step is maintained and updated
     with incremental text deltas.

4. Calls ``chat.stopStream`` to finalize.

.. important::
   Slack's streaming API enforces **mode isolation**: a stream started with
   ``task_display_mode="plan"`` can only accept ``task_update`` chunks
   (not ``markdown_text``), and vice versa.  Attempting to mix them raises
   ``streaming_mode_mismatch``.  This consumer defaults to ``"timeline"``
   mode which supports both chunk types and renders proper markdown.

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

# Slack API error code returned when chat.appendStream / chat.stopStream
# is called on a stream that has already been closed server-side (e.g. due
# to timeout).  This is the *API error code* in the response body, NOT the
# exception message — slack_sdk.errors.SlackApiError.__str__() does NOT
# reliably include this code, so we must inspect e.response.data["error"].
_STREAM_CLOSED_ERROR = "message_not_in_streaming_state"


def _is_stream_closed_error(exc: Exception) -> bool:
    """Check whether *exc* is a Slack ``message_not_in_streaming_state`` error.

    The Slack SDK raises ``SlackApiError`` whose ``str()`` typically looks
    like ``"The request to the Slack API failed. (url: …, status: 200)"``
    — the actual error code lives in ``exc.response.data["error"]``.
    We check both locations for robustness across SDK versions.
    """
    err_str = str(exc)
    if _STREAM_CLOSED_ERROR in err_str:
        return True
    # Inspect the Slack response object directly
    resp = getattr(exc, "response", None)
    if resp is not None:
        data = getattr(resp, "data", None) or {}
        if isinstance(data, dict) and data.get("error") == _STREAM_CLOSED_ERROR:
            return True
    return False

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
    # ── Stream display mode ─────────────────────────────────────
    # "timeline" (default): each step is a separate card; response
    # text is delivered via ``markdown_text`` chunks which render
    # proper Slack mrkdwn.  "plan" groups all task cards together
    # but does NOT support ``markdown_text`` — response text must
    # go through ``task_update.output`` (256-char limit, no
    # markdown rendering).  "dense" collapses consecutive tool
    # calls into a single summarized card.
    task_display_mode: str = "timeline"
    # ── Plan-mode text delivery ────────────────────────────────
    # When True and task_display_mode is "plan", response text is
    # NOT sent through TaskUpdateChunk.output (which has a 256-char
    # limit and no markdown rendering).  Instead, the stream
    # delivers only step cards (grouped under a single collapsible
    # tree), and the response text is delivered via chat.postMessage
    # after the stream completes — giving full mrkdwn rendering.
    plan_text_via_postmessage: bool = True
    # ── Keep-alive settings ──────────────────────────────────────
    # How long (seconds) with no queue activity before sending a
    # keep-alive ping.  Slack closes idle streams after ~5 min;
    # 120 s gives comfortable margin with minimal API overhead.
    keepalive_interval: float = 120.0
    # Whether keep-alive pings are enabled (default: True)
    keepalive_enabled: bool = True
    # ── Fallback message settings ─────────────────────────────────
    # Explanatory prefix prepended to the fallback chat.postMessage
    # when the stream breaks, so users aren't confused by the
    # "Something went wrong" banner.  Set to "" to disable.
    fallback_explanation: str = (
        "_The streaming display disconnected, but the response was completed successfully._\n\n"
    )


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
        format_message: Optional[Any] = None,
    ):
        self._client = client
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._config = config or SlackStreamConfig()
        self._metadata = metadata or {}
        # Callable that converts standard markdown → Slack mrkdwn.
        # Passed from SlackAdapter.format_message so the fallback
        # postMessage renders formatting correctly.
        self._format_message = format_message

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

        # Whether the response was fully delivered (either via stream or
        # fallback postMessage).  The gateway checks this to avoid duplicate
        # sends.
        self._final_content_delivered: bool = False

        # Whether chat.stopStream succeeded (used to decide if fallback
        # postMessage is needed for plan-mode text delivery).
        self._stop_stream_succeeded: bool = False

        # ── Keep-alive state ─────────────────────────────────────────
        # Timestamp of the last queue activity (delta, task, or done).
        # Used to detect idle periods where a keep-alive ping is needed.
        self._last_activity: float = time.monotonic()
        # ID of a keep-alive step currently in "in_progress" state.
        # Updated (not recreated) on each ping to avoid accumulating steps.
        self._keepalive_step_id: Optional[str] = None

    # ── Public properties ──────────────────────────────────────────────

    @property
    def stream_broken(self) -> bool:
        """Whether the Slack stream was broken mid-delivery.

        When True, the stream is no longer usable and any content that
        was buffered but not flushed has been lost to the stream.  Callers
        (e.g. the gateway) should fall back to ``chat.postMessage`` to
        deliver the response.
        """
        return self._stream_broken

    @property
    def accumulated_text(self) -> str:
        """All text received via ``on_delta``, including flushed portions."""
        return self._text_buffer

    @property
    def final_content_delivered(self) -> bool:
        """Whether the full response was successfully delivered to the user.

        True when the stream completed normally or the fallback
        ``chat.postMessage`` succeeded after a broken stream.
        """
        return self._final_content_delivered

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

                # Keep-alive ping: if no queue activity for keepalive_interval,
                # send a lightweight step update to prevent Slack from closing
                # the stream due to inactivity.
                if (
                    self._config.keepalive_enabled
                    and not self._stream_broken
                    and (now - self._last_activity) >= self._config.keepalive_interval
                ):
                    await self._send_keepalive()
                    self._last_activity = now

                await asyncio.sleep(0.05)
                continue

            # Any queue item counts as activity — reset the idle timer
            self._last_activity = time.monotonic()

            # If we had a keep-alive step, complete it now that real work arrived
            await self._complete_keepalive()

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
        """Initiate a streaming message via chat.startStream.

        The ``task_display_mode`` is configurable (default: ``"timeline"``).
        In timeline mode, the response text is delivered via
        ``markdown_text`` chunks which render proper Slack mrkdwn.
        In plan mode, text must go through ``task_update.output`` instead.

        Immediately emits an initial "Processing…" step so Slack's built-in
        "Gathering information…" placeholder is replaced as fast as possible.
        This step is completed once the first real tool step or text delta
        arrives.

        Raises on failure so the ``run()`` loop falls back to non-streaming.
        """
        resp = await self._client.chat_startStream(
            channel=self._channel_id,
            thread_ts=self._thread_ts,
            task_display_mode=self._config.task_display_mode,
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
        self._stop_stream_succeeded = False

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

    def _make_markdown_text_chunk(self, text: str) -> Any:
        """Build a markdown_text chunk for Slack's streaming API.

        ``markdown_text`` chunks render proper Slack mrkdwn (bold, italic,
        links, headers, code blocks, etc.) in the streamed message.  They
        are only valid in ``timeline`` and ``dense`` display modes — NOT
        in ``plan`` mode (which raises ``streaming_mode_mismatch``).
        """
        try:
            from slack_sdk.models.messages.chunk import MarkdownTextChunk
            return MarkdownTextChunk(text=text)
        except ImportError:
            # Fallback for slack_sdk < 3.35
            return {"type": "markdown_text", "text": text}

    def _is_plan_mode(self) -> bool:
        """Whether the stream was started in plan display mode."""
        return self._config.task_display_mode == "plan"

    def _skip_in_stream_text(self) -> bool:
        """Whether response text should be delivered via postMessage instead of in-stream.

        When ``plan_text_via_postmessage`` is True and the display mode is ``plan``,
        text is not sent through TaskUpdateChunk.output (256-char limit, no markdown).
        Instead, it's delivered via chat.postMessage after the stream completes,
        giving full mrkdwn rendering while keeping the grouped step cards.
        """
        return self._is_plan_mode() and self._config.plan_text_via_postmessage

    async def _flush_text(self) -> None:
        """Flush accumulated text deltas to the stream.

        **Timeline / dense mode** (default): Text is delivered via
        ``markdown_text`` chunks which render proper Slack mrkdwn with
        a 12 000-char limit per call.

        **Plan mode with plan_text_via_postmessage** (default): Text
        is NOT sent through the stream at all — it's accumulated
        in ``_text_buffer`` and delivered via ``chat.postMessage``
        after the stream completes, giving full mrkdwn rendering
        while keeping the grouped step-card layout.

        **Plan mode (legacy)**: ``markdown_text`` is not allowed —
        text must go through ``TaskUpdateChunk.output`` (256-char
        limit, no markdown rendering).  We maintain a "Response"
        step and update it with the delta each flush.

        Slack's ``chat.appendStream`` **appends** content across calls,
        so we must send only the delta — the new text since the last
        flush — not the full buffer.
        """
        if not self._text_buffer or not self._stream_ts or self._stream_broken:
            return
        # When plan_text_via_postmessage is active, skip in-stream text
        # delivery — text will be sent via postMessage after finalization.
        if self._skip_in_stream_text():
            return
        # Only send text we haven't already flushed
        new_text = self._text_buffer[self._total_text_sent:]
        if not new_text:
            return
        try:
            if self._is_plan_mode():
                # Plan mode: text goes through task_update.output
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
            else:
                # Timeline / dense mode: text goes through markdown_text
                chunk = self._make_markdown_text_chunk(new_text)
                await self._client.chat_appendStream(
                    channel=self._channel_id,
                    ts=self._stream_ts,
                    chunks=[chunk],
                )
            self._total_text_sent = len(self._text_buffer)
            # Don't clear the buffer — we need the full text to compute
            # the delta for the next flush.
        except Exception as e:
            if _is_stream_closed_error(e):
                self._stream_broken = True
                logger.debug("[SlackStream] Stream closed during text flush — suppressing further calls")
            else:
                logger.warning("[SlackStream] flush text failed: %s", e)

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
            if _is_stream_closed_error(e):
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

    async def _send_keepalive(self) -> None:
        """Send a keep-alive ping to prevent Slack from closing the idle stream.

        Updates an existing keep-alive step (or creates one if this is the
        first ping) with an ``in_progress`` status and a timestamp.  The step
        is completed when real activity resumes (see ``_complete_keepalive``).
        Reusing a single step avoids accumulating steps during long tool runs.
        """
        if self._stream_broken or not self._stream_ts:
            return

        elapsed = time.monotonic() - self._last_activity
        # Human-friendly label: "Still working… (2m)"
        mins = int(elapsed) // 60
        secs = int(elapsed) % 60
        if mins > 0:
            label = f"Still working… ({mins}m {secs}s)"
        else:
            label = f"Still working… ({secs}s)"

        if self._keepalive_step_id is None:
            # First keep-alive — create a new step
            self._task_counter += 1
            self._keepalive_step_id = f"keepalive_{self._task_counter}"
            try:
                await self._send_task_step(
                    self._keepalive_step_id, label, "in_progress", ""
                )
                logger.debug(
                    "[SlackStream] Keep-alive ping sent (first, %.0fs idle)",
                    elapsed,
                )
            except Exception as e:
                if _is_stream_closed_error(e):
                    self._stream_broken = True
                else:
                    logger.debug("[SlackStream] Keep-alive ping failed: %s", e)
                self._keepalive_step_id = None
        else:
            # Subsequent pings — update the existing step with refreshed label
            try:
                await self._send_task_step(
                    self._keepalive_step_id, label, "in_progress", ""
                )
                logger.debug(
                    "[SlackStream] Keep-alive ping sent (update, %.0fs idle)",
                    elapsed,
                )
            except Exception as e:
                if _is_stream_closed_error(e):
                    self._stream_broken = True
                else:
                    logger.debug("[SlackStream] Keep-alive ping update failed: %s", e)
                self._keepalive_step_id = None

    async def _complete_keepalive(self) -> None:
        """Complete the keep-alive step (if active) when real work arrives.

        Called from the run loop whenever a queue item is dequeued — i.e.
        a delta, task event, or done signal.  The keep-alive step is
        marked complete and cleared so the next idle period creates a fresh one.
        """
        if self._keepalive_step_id is None or self._stream_broken:
            return
        step_id = self._keepalive_step_id
        self._keepalive_step_id = None
        try:
            await self._send_task_step(step_id, "Still working…", "complete", "")
        except Exception as e:
            if _is_stream_closed_error(e):
                self._stream_broken = True
            else:
                logger.debug("[SlackStream] Keep-alive complete failed: %s", e)

    async def _finalize(self) -> None:
        """Flush remaining text, complete active tasks, and stop the stream.

        Handles ``message_not_in_streaming_state`` gracefully — if Slack has
        already closed the stream (server-side timeout), we can't append any
        more chunks but the content already rendered is intact.

        When the stream is broken (either before or during finalization),
        falls back to ``chat.postMessage`` to deliver the accumulated text
        so the user isn't left staring at Slack's "Something went wrong"
        step card with no content.
        """
        if self._stream_broken:
            # Stream is already closed server-side — try stopStream for
            # cleanup, then fall back to chat.postMessage with any text
            # we accumulated so the user still gets the content.
            await self._try_stop_stream()
            await self._fallback_post_message()
            return

        # Complete any still-active tool/thinking tasks
        for task_id, name in list(self._active_tasks.items()):
            try:
                await self._send_task_step(task_id, name, "complete", "")
            except Exception as e:
                if _is_stream_closed_error(e):
                    logger.debug("[SlackStream] Stream already closed, skipping task completion for %s", name)
                    # Stream broke during finalization — fall back immediately
                    self._stream_broken = True
                    await self._try_stop_stream()
                    await self._fallback_post_message()
                    return
                pass

        # Flush remaining text
        if self._skip_in_stream_text():
            # Plan mode with plan_text_via_postmessage: don't send text
            # through the stream at all — deliver via postMessage after
            # stopping the stream for full mrkdwn rendering.
            pass
        elif self._text_buffer:
            try:
                new_text = self._text_buffer[self._total_text_sent:]
                if new_text:
                    if self._is_plan_mode():
                        # Plan mode: complete the Response step with remaining text
                        step_id = await self._ensure_response_step()
                        chunk = self._make_task_chunk(
                            step_id,
                            self._config.response_step_title,
                            "complete",
                            output=new_text,
                        )
                    else:
                        # Timeline / dense mode: final markdown_text chunk
                        chunk = self._make_markdown_text_chunk(new_text)
                    await self._client.chat_appendStream(
                        channel=self._channel_id,
                        ts=self._stream_ts,
                        chunks=[chunk],
                    )
                # In plan mode, also complete the Response step (even if no new text)
                if self._is_plan_mode() and self._response_step_id:
                    chunk = self._make_task_chunk(
                        self._response_step_id,
                        self._config.response_step_title,
                        "complete",
                    )
                    await self._client.chat_appendStream(
                        channel=self._channel_id,
                        ts=self._stream_ts,
                        chunks=[chunk],
                    )
                self._text_buffer = ""
            except Exception as e:
                if _is_stream_closed_error(e):
                    logger.debug(
                        "[SlackStream] Stream already closed before final text flush — "
                        "falling back to postMessage."
                    )
                    # Stream broke during final text delivery — fall back
                    self._stream_broken = True
                    await self._try_stop_stream()
                    await self._fallback_post_message()
                    return
                else:
                    logger.warning("[SlackStream] finalize text flush failed: %s", e)
        elif self._is_plan_mode() and self._response_step_id:
            # Plan mode with no text buffer but an active Response step — complete it
            try:
                chunk = self._make_task_chunk(
                    self._response_step_id,
                    self._config.response_step_title,
                    "complete",
                )
                await self._client.chat_appendStream(
                    channel=self._channel_id,
                    ts=self._stream_ts,
                    chunks=[chunk],
                )
            except Exception as e:
                if _is_stream_closed_error(e):
                    self._stream_broken = True
                else:
                    logger.warning("[SlackStream] complete Response step failed: %s", e)

        # Stop the stream — include the response text in the stopStream
        # call so the stream card contains both the grouped step cards
        # AND the response text.  This keeps the steps visible after
        # completion rather than disappearing when a separate postMessage
        # lands below the stream card.
        _plan_text = ""
        if self._skip_in_stream_text() and self._text_buffer:
            # Plan mode with plan_text_via_postmessage: deliver text
            # inside the stopStream call for persistent step display.
            _plan_text = self._text_buffer

        await self._try_stop_stream(final_text=_plan_text)

        # If stopStream rejected markdown_text (plan mode isolation) and
        # we still have text to deliver, fall back to postMessage.
        if _plan_text and not self._final_content_delivered:
            await self._fallback_post_message(intentional=True)

        self._started = False
        if not self._final_content_delivered:
            self._final_content_delivered = True

    async def _fallback_post_message(self, *, intentional: bool = False) -> None:
        """Deliver accumulated text via chat.postMessage.

        When *intentional* is False (default): the stream broke mid-delivery.
        Attempts to **replace** the broken stream message (which shows
        "Something went wrong") by calling ``chat.update`` on the stream's
        own ``ts`` with properly formatted mrkdwn text.  If that succeeds,
        the error card is replaced with the actual response content and
        the user never sees the "Something went wrong" banner.

        When *intentional* is True: this is a planned delivery (e.g.
        plan_text_via_postmessage mode).  Skip chat.update (don't
        overwrite the step cards) and don't prepend the fallback
        explanation.  Just post the text as a new thread message.

        The text is converted from standard markdown to Slack mrkdwn via
        the ``format_message`` callable (passed from ``SlackAdapter`` at
        construction time) so links, headers, bold/italic, and code blocks
        render correctly.
        """
        text = self._text_buffer
        if not text:
            logger.debug("[SlackStream] No text to deliver via fallback postMessage")
            return

        # Convert markdown → Slack mrkdwn if the formatter is available
        if self._format_message:
            try:
                text = self._format_message(text)
            except Exception as e:
                logger.debug("[SlackStream] format_message failed in fallback: %s", e)

        if not intentional:
            # Strategy 1: Update the broken stream message in-place to replace
            # the "Something went wrong" card with properly formatted content.
            if self._stream_ts:
                try:
                    update_kwargs = {
                        "channel": self._channel_id,
                        "ts": self._stream_ts,
                        "text": text,
                    }
                    result = await self._client.chat_update(**update_kwargs)
                    ok = result.get("ok", True) if isinstance(result, dict) else getattr(result, "data", {}).get("ok", True)
                    if ok is not False:
                        logger.info(
                            "[SlackStream] Replaced broken stream message via chat.update "
                            "(channel=%s stream_ts=%s, %d chars)",
                            self._channel_id, self._stream_ts, len(text),
                        )
                        self._final_content_delivered = True
                        return
                    else:
                        error = result.get("error", "unknown") if isinstance(result, dict) else "unknown"
                        logger.debug("[SlackStream] chat.update on stream msg failed: %s — falling back to postMessage", error)
                except Exception as e:
                    logger.debug("[SlackStream] chat.update on stream msg raised: %s — falling back to postMessage", e)

        # Strategy 2: Post a new message in the thread.
        # When intentional, skip the "disconnected" explanation prefix.
        # When broken-stream, prepend it so users understand the banner.
        explanation = "" if intentional else (self._config.fallback_explanation or "")
        if explanation and self._format_message:
            # Format the explanation too (it may contain mrkdwn like _italic_)
            try:
                explanation = self._format_message(explanation)
            except Exception:
                pass
        delivered_text = explanation + text

        try:
            kwargs = {
                "channel": self._channel_id,
                "text": delivered_text,
                "mrkdwn": True,
            }
            if self._thread_ts:
                kwargs["thread_ts"] = self._thread_ts
            result = await self._client.chat_postMessage(**kwargs)
            ok = result.get("ok", True) if isinstance(result, dict) else getattr(result, "data", {}).get("ok", True)
            if ok is not False:
                logger.info(
                    "[SlackStream] Delivered %d chars via fallback postMessage "
                    "after broken stream (channel=%s thread=%s)",
                    len(delivered_text), self._channel_id, self._thread_ts,
                )
                self._final_content_delivered = True
            else:
                error = result.get("error", "unknown") if isinstance(result, dict) else "unknown"
                logger.warning("[SlackStream] Fallback postMessage failed: %s", error)
        except Exception as e:
            logger.warning(
                "[SlackStream] Fallback postMessage failed: %s", e, exc_info=True,
            )

    async def _try_stop_stream(
        self,
        *,
        final_text: str = "",
        chunks: Optional[list] = None,
    ) -> None:
        """Best-effort call to chat.stopStream — safe even if stream is already closed.

        When *final_text* is provided and the stream is in plan mode, the text
        is included as ``markdown_text`` in the ``stopStream`` call so the
        finalised stream card contains both the grouped step cards AND the
        response text — preventing the steps from disappearing after completion.
        If Slack rejects ``markdown_text`` in plan mode (``streaming_mode_mismatch``),
        falls back to ``blocks`` chunks, then to a separate ``postMessage``.
        """
        if not self._stream_ts:
            return

        kwargs: Dict[str, Any] = {
            "channel": self._channel_id,
            "ts": self._stream_ts,
        }

        # Include final text in the stopStream call when provided.
        # This keeps step cards visible inside the stream card rather
        # than delivering text via a separate postMessage that may
        # cause Slack to collapse/hide the completed step cards.
        if final_text:
            # Format markdown → Slack mrkdwn
            formatted_text = final_text
            if self._format_message:
                try:
                    formatted_text = self._format_message(final_text)
                except Exception:
                    pass
            kwargs["markdown_text"] = formatted_text

        if chunks:
            kwargs["chunks"] = chunks

        try:
            await self._client.chat_stopStream(**kwargs)
            logger.debug(
                "[SlackStream] Stopped stream with %d chars final text: stream_ts=%s",
                len(final_text) if final_text else 0,
                self._stream_ts,
            )
            # Track that final content was delivered inside the stream
            if final_text:
                self._final_content_delivered = True
            self._stop_stream_succeeded = True
        except Exception as e:
            if _is_stream_closed_error(e):
                logger.debug("[SlackStream] stopStream: stream already closed server-side")
            else:
                # If markdown_text was rejected due to mode isolation in plan mode,
                # try again without it — we'll deliver text via postMessage instead.
                err_data = getattr(e, "response", None)
                err_code = ""
                if err_data is not None:
                    data = getattr(err_data, "data", None) or {}
                    err_code = data.get("error", "")
                if "streaming_mode_mismatch" in (str(e) + err_code):
                    logger.debug(
                        "[SlackStream] stopStream rejected markdown_text in plan mode — "
                        "retrying without text for postMessage delivery"
                    )
                    # Retry stopStream without markdown_text
                    retry_kwargs = {
                        "channel": self._channel_id,
                        "ts": self._stream_ts,
                    }
                    try:
                        await self._client.chat_stopStream(**retry_kwargs)
                        self._stop_stream_succeeded = True
                    except Exception as retry_e:
                        if _is_stream_closed_error(retry_e):
                            logger.debug("[SlackStream] stopStream retry: stream already closed")
                        else:
                            logger.warning("[SlackStream] stopStream retry failed: %s", retry_e)
                else:
                    logger.warning("[SlackStream] stopStream failed: %s", e)
