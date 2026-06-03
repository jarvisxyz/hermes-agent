"""
Slack Block Kit builder library for Hermes.

Provides structured block construction for rich Slack messages:
- Section blocks with markdown text and optional accessories
- Header blocks for prominent titles
- Context blocks for metadata / status lines
- Divider blocks for visual separation
- Interactive blocks: buttons, static select (dropdowns), overflow menus
- Action groups for approval, clarify, and session controls
- Response status footers (model, tokens, duration)
- Collapsible thinking/reasoning blocks

All builders return plain dicts matching Slack's Block Kit spec.
The SlackAdapter assembles these into the ``blocks`` array for
``chat_postMessage`` / ``chat_update`` calls.

Block Kit reference: https://api.slack.com/reference/block-kit
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Sequence


# ── Constants ──────────────────────────────────────────────────────────────────

MAX_BLOCKS_PER_MESSAGE = 50
MAX_TEXT_LENGTH_SECTION = 3000
MAX_TEXT_LENGTH_HEADER = 150
MAX_TEXT_LENGTH_CONTEXT = 3000
MAX_TEXT_LENGTH_BUTTON = 75
MAX_TEXT_LENGTH_PLACEHOLDER = 150
MAX_OPTIONS_SELECT = 100  # Slack limit for static_select options
MAX_OPTIONS_OVERFLOW = 25  # Slack limit for overflow menus
MAX_SECTIONS_FIELDS = 10


# ── Primitive block builders ──────────────────────────────────────────────────

def section_block(
    text: str,
    *,
    text_type: str = "mrkdwn",
    block_id: str | None = None,
    accessory: dict | None = None,
    fields: list[dict] | None = None,
) -> dict:
    """Build a ``section`` block with mrkdwn text.

    Optionally carries an ``accessory`` (image, button, etc.) or a
    ``fields`` list (up to 10 short text objects displayed in a two-column
    table layout).
    """
    if len(text) > MAX_TEXT_LENGTH_SECTION:
        text = text[: MAX_TEXT_LENGTH_SECTION - 3] + "..."

    block: dict = {
        "type": "section",
        "text": {"type": text_type, "text": text},
    }
    if block_id:
        block["block_id"] = block_id
    if accessory:
        block["accessory"] = accessory
    if fields:
        block["fields"] = fields[:MAX_SECTIONS_FIELDS]
    return block


def header_block(text: str, *, block_id: str | None = None) -> dict:
    """Build a ``header`` block (plain_text, large bold text)."""
    if len(text) > MAX_TEXT_LENGTH_HEADER:
        text = text[: MAX_TEXT_LENGTH_HEADER - 3] + "..."
    block: dict = {
        "type": "header",
        "text": {"type": "plain_text", "text": text},
    }
    if block_id:
        block["block_id"] = block_id
    return block


def context_block(elements: list[dict], *, block_id: str | None = None) -> dict:
    """Build a ``context`` block (small grey text / images)."""
    block: dict = {"type": "context", "elements": elements}
    if block_id:
        block["block_id"] = block_id
    return block


def divider_block(*, block_id: str | None = None) -> dict:
    """Build a ``divider`` block."""
    block: dict = {"type": "divider"}
    if block_id:
        block["block_id"] = block_id
    return block


def image_block(
    image_url: str,
    alt_text: str,
    *,
    title: str | None = None,
    block_id: str | None = None,
) -> dict:
    """Build an ``image`` block."""
    block: dict = {
        "type": "image",
        "image_url": image_url,
        "alt_text": alt_text,
    }
    if title:
        block["title"] = {"type": "plain_text", "text": title}
    if block_id:
        block["block_id"] = block_id
    return block


def actions_block(
    elements: list[dict],
    *,
    block_id: str | None = None,
) -> dict:
    """Build an ``actions`` block containing interactive elements."""
    block: dict = {"type": "actions", "elements": elements}
    if block_id:
        block["block_id"] = block_id
    return block


# ── Interactive element builders ───────────────────────────────────────────────

def button_element(
    text: str,
    action_id: str,
    *,
    value: str = "",
    style: str | None = None,
    confirm: dict | None = None,
) -> dict:
    """Build a ``button`` interactive element.

    ``style``: "primary" (green), "danger" (red), or None (default grey).
    ``confirm``: optional confirmation dialog dict.
    """
    if len(text) > MAX_TEXT_LENGTH_BUTTON:
        text = text[: MAX_TEXT_LENGTH_BUTTON - 3] + "..."
    element: dict = {
        "type": "button",
        "text": {"type": "plain_text", "text": text},
        "action_id": action_id,
    }
    if value:
        element["value"] = value
    if style:
        element["style"] = style
    if confirm:
        element["confirm"] = confirm
    return element


def static_select_element(
    action_id: str,
    options: list[dict],
    *,
    placeholder: str = "Select an option",
    initial_option: dict | None = None,
) -> dict:
    """Build a ``static_select`` dropdown element.

    ``options``: list of ``{"text": {"type": "plain_text", "text": "..."},
    "value": "..."}`` dicts (max 100).
    """
    if len(placeholder) > MAX_TEXT_LENGTH_PLACEHOLDER:
        placeholder = placeholder[: MAX_TEXT_LENGTH_PLACEHOLDER - 3] + "..."
    element: dict = {
        "type": "static_select",
        "action_id": action_id,
        "placeholder": {"type": "plain_text", "text": placeholder},
        "options": options[:MAX_OPTIONS_SELECT],
    }
    if initial_option:
        element["initial_option"] = initial_option
    return element


def option_object(text: str, value: str) -> dict:
    """Build a single option for static_select or overflow."""
    return {
        "text": {"type": "plain_text", "text": text[:75]},
        "value": value,
    }


def option_group_object(label: str, options: list[dict]) -> dict:
    """Build an option_group for static_select with grouped options."""
    return {
        "label": {"type": "plain_text", "text": label[:75]},
        "options": options[:MAX_OPTIONS_SELECT],
    }


def overflow_element(
    action_id: str,
    options: list[dict],
) -> dict:
    """Build an ``overflow`` menu element (max 25 options)."""
    return {
        "type": "overflow",
        "action_id": action_id,
        "options": options[:MAX_OPTIONS_OVERFLOW],
    }


def confirmation_dialog(
    title: str,
    text: str,
    confirm: str = "Confirm",
    deny: str = "Cancel",
) -> dict:
    """Build a confirmation dialog object for button elements."""
    return {
        "title": {"type": "plain_text", "text": title},
        "text": {"type": "mrkdwn", "text": text},
        "confirm": {"type": "plain_text", "text": confirm},
        "deny": {"type": "plain_text", "text": deny},
    }


# ── Composite block builders (Hermes-specific) ────────────────────────────────

def build_approval_blocks(
    command_preview: str,
    session_key: str,
    *,
    dangerous: bool = False,
) -> list[dict]:
    """Build Block Kit blocks for a command approval prompt.

    Renders a section with the command preview and an actions row with
    Approve Once / Approve Session / Approve Always / Deny buttons.
    If ``dangerous`` is True, the deny button gets a confirm dialog.
    """
    blocks: list[dict] = [
        section_block(
            f"⚠️ *Command approval required*\n```{command_preview[:2800]}```",
            block_id=f"hermes_approval_{session_key}",
        ),
    ]

    buttons = [
        button_element("✅ Approve Once", "hermes_approve_once", value=session_key),
        button_element("🔓 Session", "hermes_approve_session", value=session_key, style="primary"),
        button_element("♾️ Always", "hermes_approve_always", value=session_key),
        button_element(
            "❌ Deny", "hermes_deny", value=session_key, style="danger",
            confirm=confirmation_dialog(
                "Deny command?",
                f"This will deny:\n```{command_preview[:200]}```",
                "Deny",
            ) if dangerous else None,
        ),
    ]

    blocks.append(actions_block(buttons, block_id=f"hermes_approval_actions_{session_key}"))
    return blocks


def build_resolved_approval_blocks(
    original_text: str,
    decision_text: str,
) -> list[dict]:
    """Build blocks for a resolved approval (after button click).

    Replaces the interactive buttons with a context line showing the decision.
    """
    return [
        section_block(original_text or "Command approval request"),
        context_block([{"type": "mrkdwn", "text": decision_text}]),
    ]


def build_clarify_blocks(
    question: str,
    choices: list[str],
    clarify_id: str,
) -> list[dict]:
    """Build Block Kit blocks for a clarify (disambiguation) prompt.

    If ``choices`` has ≤5 items, renders one button per choice plus an
    "Other" button for free-text. Longer choice lists fall back to a
    numbered list in the section text with just an "Other" button.
    """
    blocks: list[dict] = [
        section_block(
            f"❓ *{question[:2900]}*" if len(question) <= 2900
            else f"❓ *{question[:2900]}...*",
            block_id=f"hermes_clarify_{clarify_id}",
        ),
    ]

    if choices and len(choices) <= 5:
        buttons = []
        for idx, choice in enumerate(choices):
            label = str(choice)[:MAX_TEXT_LENGTH_BUTTON]
            buttons.append(
                button_element(label, "hermes_clarify_choice", value=f"{clarify_id}:{idx}")
            )
        buttons.append(
            button_element("✏️ Other", "hermes_clarify_other", value=clarify_id)
        )
        blocks.append(actions_block(buttons, block_id=f"hermes_clarify_actions_{clarify_id}"))
    elif choices:
        option_lines = "\n".join(f"  {i + 1}. {str(c)}" for i, c in enumerate(choices))
        blocks[0] = section_block(
            f"❓ *{question[:2200]}*\n\n{option_lines}",
            block_id=f"hermes_clarify_{clarify_id}",
        )
        blocks.append(
            actions_block(
                [button_element("✏️ Other", "hermes_clarify_other", value=clarify_id)],
                block_id=f"hermes_clarify_actions_{clarify_id}",
            )
        )

    return blocks


def build_status_footer(
    model: str = "",
    provider: str = "",
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    duration_s: float | None = None,
    session_id: str = "",
) -> dict:
    """Build a context block footer showing response metadata.

    Renders model/provider, token counts, duration, and session ID
    in a compact context block.  Designed to be appended at the end
    of a message's block list.
    """
    parts: list[str] = []
    if model:
        label = model
        if provider:
            label = f"{model} ({provider})"
        parts.append(f"🤖 {label}")
    if tokens_in is not None and tokens_out is not None:
        parts.append(f"📊 {tokens_in:,}→{tokens_out:,} tokens")
    elif tokens_out is not None:
        parts.append(f"📊 {tokens_out:,} tokens")
    if duration_s is not None:
        if duration_s < 1:
            parts.append(f"⏱️ {duration_s * 1000:.0f}ms")
        elif duration_s < 60:
            parts.append(f"⏱️ {duration_s:.1f}s")
        else:
            m, s = divmod(int(duration_s), 60)
            parts.append(f"⏱️ {m}m{s:02d}s")
    if session_id:
        short_id = session_id[:8] if len(session_id) > 8 else session_id
        parts.append(f"📋 `{short_id}`")

    if not parts:
        return context_block([{"type": "mrkdwn", "text": "_Hermes_"}])

    return context_block([{"type": "mrkdwn", "text": " │ ".join(parts)}])


def build_thinking_toggle_block(
    session_key: str,
    *,
    currently_enabled: bool = True,
) -> dict:
    """Build an actions block with a thinking/reasoning toggle button.

    The button toggles between showing and hiding reasoning output.
    Uses a single button whose label reflects the current state.
    """
    if currently_enabled:
        label = "🧠 Hide Thinking"
        value = f"{session_key}:off"
    else:
        label = "🧠 Show Thinking"
        value = f"{session_key}:on"

    return actions_block(
        [button_element(label, "hermes_thinking_toggle", value=value)],
        block_id=f"hermes_thinking_{session_key}",
    )


def build_model_selector_block(
    session_key: str,
    models: list[dict],
    *,
    current_model: str = "",
) -> dict:
    """Build an actions block with a model/provider dropdown selector.

    ``models``: list of ``{"label": "Claude Sonnet 4", "value": "anthropic/claude-sonnet-4"}`` dicts.
    The current model is pre-selected if it matches an option value.
    """
    options = [option_object(m["label"], m["value"]) for m in models]
    initial = None
    if current_model:
        initial = next((o for o in options if o["value"] == current_model), None)

    return actions_block(
        [
            static_select_element(
                "hermes_model_select",
                options,
                placeholder="Switch model…",
                initial_option=initial,
            )
        ],
        block_id=f"hermes_model_{session_key}",
    )


def build_reasoning_level_block(
    session_key: str,
    levels: list[str] | None = None,
    *,
    current_level: str = "",
) -> dict:
    """Build an actions block with a reasoning effort dropdown.

    ``levels``: list of reasoning levels (default: none, minimal, low,
    medium, high, xhigh).
    """
    if levels is None:
        levels = ["none", "minimal", "low", "medium", "high", "xhigh"]

    options = [option_object(lv.title(), f"reasoning:{lv}") for lv in levels]
    initial = next((o for o in options if o["value"] == f"reasoning:{current_level}"), None)

    return actions_block(
        [
            static_select_element(
                "hermes_reasoning_select",
                options,
                placeholder="Reasoning effort…",
                initial_option=initial if current_level else None,
            )
        ],
        block_id=f"hermes_reasoning_{session_key}",
    )


def build_tool_progress_blocks(
    tool_lines: list[str],
    *,
    running: bool = True,
    max_chars: int = 2900,
) -> list[dict]:
    """Build blocks for tool-call progress display.

    Renders accumulated tool progress lines in a section block with a
    context indicator.  When ``running`` is True, shows "⏳ Running tools…"
    in the context footer; when False, shows "✅ Tools complete".
    """
    if not tool_lines:
        return []

    text = "\n".join(str(line) for line in tool_lines)
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."

    blocks = [
        section_block(text),
        context_block(
            [
                {
                    "type": "mrkdwn",
                    "text": "⏳ _Running tools…_" if running else "✅ _Tools complete_",
                }
            ]
        ),
    ]
    return blocks


def build_slash_confirm_blocks(
    title: str,
    body_text: str,
    session_key: str,
    confirm_id: str,
) -> list[dict]:
    """Build Block Kit blocks for a slash-confirmation prompt.

    Renders a section with the prompt body and action buttons:
    Confirm Once / Always Approve / Cancel.
    """
    blocks: list[dict] = [
        section_block(
            f"*{title}*\n{body_text[:2600]}",
            block_id=f"hermes_confirm_{confirm_id}",
        ),
    ]

    buttons = [
        button_element("✅ Once", "hermes_confirm_once", value=f"{session_key}|{confirm_id}"),
        button_element("♾️ Always", "hermes_confirm_always", value=f"{session_key}|{confirm_id}", style="primary"),
        button_element("❌ Cancel", "hermes_confirm_cancel", value=f"{session_key}|{confirm_id}", style="danger"),
    ]

    blocks.append(actions_block(buttons, block_id=f"hermes_confirm_actions_{confirm_id}"))
    return blocks


def build_resolved_confirm_blocks(
    original_text: str,
    decision_text: str,
) -> list[dict]:
    """Build blocks for a resolved slash-confirm (after button click)."""
    return [
        section_block(original_text or "Confirmation prompt"),
        context_block([{"type": "mrkdwn", "text": decision_text}]),
    ]


# ── Markdown-to-blocks converter (enhanced) ───────────────────────────────────

def markdown_to_blocks(
    content: str,
    *,
    max_text_length: int = MAX_TEXT_LENGTH_SECTION,
    include_status_footer: bool = False,
    status_model: str = "",
    status_provider: str = "",
    status_tokens_in: int | None = None,
    status_tokens_out: int | None = None,
    status_duration_s: float | None = None,
    status_session_id: str = "",
) -> list[dict]:
    """Convert a markdown string into Slack Block Kit blocks.

    Enhancements over a naive converter:
    - Splits on markdown headers (## Title) → separate section blocks
    - Converts code blocks into section blocks with ``` fencing
    - Adds dividers between sections
    - Headers render as bold mrkdwn
    - Respects Slack's 50-block limit
    - Optionally appends a status footer context block

    This is intentionally conservative: it does NOT attempt to convert
    lists, tables, or inline formatting into separate block types. Those
    remain as mrkdwn text within their section, which Slack renders well.
    The primary win is header-based section splitting for visual hierarchy.
    """
    if not content:
        return []

    # Split on markdown headers (## Title, ### Title, etc.)
    # Each header starts a new section block.
    parts = re.split(r"\n(?=#{1,6}\s)", content)

    blocks: list[dict] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Slack section text limit
        text = part if len(part) <= max_text_length else part[: max_text_length - 3] + "..."

        # Convert the header line to bold mrkdwn if present
        lines = text.split("\n")
        first_line = lines[0].strip()
        if re.match(r"^#{1,6}\s+", first_line):
            header_match = re.match(r"^(#{1,6})\s+(.+)$", first_line)
            if header_match:
                level = len(header_match.group(1))
                title = header_match.group(2).strip()
                # Strip any remaining markdown bold/italic markers
                title = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", title)
                if level <= 2:
                    lines[0] = f"*{title}*"
                else:
                    lines[0] = f"_{title}_"
            text = "\n".join(lines)

        blocks.append(section_block(text))

        if len(blocks) >= 49:  # Reserve 1 slot for potential footer
            break

    # Add dividers between sections if there are 2+ section blocks
    if len(blocks) > 1:
        interleaved: list[dict] = []
        for i, block in enumerate(blocks):
            interleaved.append(block)
            if i < len(blocks) - 1:
                interleaved.append(divider_block())
        blocks = interleaved

    # Status footer
    if include_status_footer:
        footer = build_status_footer(
            model=status_model,
            provider=status_provider,
            tokens_in=status_tokens_in,
            tokens_out=status_tokens_out,
            duration_s=status_duration_s,
            session_id=status_session_id,
        )
        blocks.append(footer)

    # Slack allows max 50 blocks per message
    return blocks[:MAX_BLOCKS_PER_MESSAGE]


def build_thinking_reveal_blocks(
    thinking_text: str,
    session_key: str,
    *,
    max_chars: int = 2900,
) -> list[dict]:
    """Build blocks that display thinking/reasoning content with a
    toggle to collapse it.

    Shows the thinking text in a section block with a distinct visual
    marker and a "Hide Thinking" button.  The block_id carries the
    session key so the action handler can update the right message.
    """
    display = thinking_text[:max_chars]
    if len(thinking_text) > max_chars:
        display += "..."

    return [
        section_block(
            f"🧠 *Thinking:*\n> {display}",
            block_id=f"hermes_thinking_content_{session_key}",
        ),
        build_thinking_toggle_block(session_key, currently_enabled=True),
    ]


def build_thinking_collapsed_block(
    summary: str,
    session_key: str,
) -> dict:
    """Build a single context block indicating thinking was used but
    collapsed, with a button to expand it.
    """
    return build_thinking_toggle_block(session_key, currently_enabled=False)


def build_session_controls_block(
    session_key: str,
    *,
    include_model_select: bool = False,
    models: list[dict] | None = None,
    current_model: str = "",
    include_reasoning: bool = False,
    current_reasoning: str = "",
) -> list[dict]:
    """Build a row of session control interactive elements.

    Combines optional model selector dropdown, reasoning level
    dropdown, and an overflow menu with session actions (new session,
    reset, etc.) into a single actions block or pair of actions blocks.
    """
    elements: list[dict] = []
    blocks: list[dict] = []

    if include_model_select and models:
        select = static_select_element(
            "hermes_model_select",
            [option_object(m["label"], m["value"]) for m in models],
            placeholder="Switch model…",
            initial_option=next(
                (option_object(m["label"], m["value"]) for m in models if m["value"] == current_model),
                None,
            ) if current_model else None,
        )
        elements.append(select)

    if include_reasoning:
        levels = ["none", "minimal", "low", "medium", "high", "xhigh"]
        options = [option_object(lv.title(), f"reasoning:{lv}") for lv in levels]
        initial = next(
            (o for o in options if o["value"] == f"reasoning:{current_reasoning}"),
            None,
        )
        elements.append(
            static_select_element(
                "hermes_reasoning_select",
                options,
                placeholder="Reasoning…",
                initial_option=initial if current_reasoning else None,
            )
        )

    if elements:
        blocks.append(
            actions_block(elements, block_id=f"hermes_controls_{session_key}")
        )

    return blocks
