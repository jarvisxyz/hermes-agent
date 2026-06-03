"""Tests for the Slack Block Kit builder library (slack_blocks.py).

These are pure unit tests — no Slack API calls, no async, no adapter
required.  Each builder function is a pure function that returns dicts,
so we validate structure, limits, and edge cases directly.
"""

import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Ensure repo root importable
# ---------------------------------------------------------------------------
_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)

from gateway.platforms.slack_blocks import (
    MAX_BLOCKS_PER_MESSAGE,
    MAX_TEXT_LENGTH_SECTION,
    MAX_TEXT_LENGTH_BUTTON,
    MAX_OPTIONS_SELECT,
    MAX_SECTIONS_FIELDS,
    section_block,
    header_block,
    context_block,
    divider_block,
    actions_block,
    button_element,
    static_select_element,
    option_object,
    option_group_object,
    overflow_element,
    confirmation_dialog,
    build_approval_blocks,
    build_resolved_approval_blocks,
    build_slash_confirm_blocks,
    build_resolved_confirm_blocks,
    build_clarify_blocks,
    build_status_footer,
    build_thinking_toggle_block,
    build_thinking_reveal_blocks,
    build_thinking_collapsed_block,
    build_model_selector_block,
    build_reasoning_level_block,
    build_tool_progress_blocks,
    build_session_controls_block,
    markdown_to_blocks,
)


# ===========================================================================
# Primitive block builders
# ===========================================================================


class TestSectionBlock:
    def test_basic_section(self):
        b = section_block("Hello world")
        assert b["type"] == "section"
        assert b["text"]["type"] == "mrkdwn"
        assert b["text"]["text"] == "Hello world"
        assert "block_id" not in b
        assert "accessory" not in b

    def test_block_id(self):
        b = section_block("x", block_id="my_block")
        assert b["block_id"] == "my_block"

    def test_accessory(self):
        acc = {"type": "image", "image_url": "https://example.com/img.png", "alt_text": "alt"}
        b = section_block("with image", accessory=acc)
        assert b["accessory"] == acc

    def test_fields_truncation(self):
        fields = [{"type": "mrkdwn", "text": f"field {i}"} for i in range(20)]
        b = section_block("x", fields=fields)
        assert len(b["fields"]) == MAX_SECTIONS_FIELDS

    def test_text_truncation(self):
        long_text = "a" * 5000
        b = section_block(long_text)
        assert len(b["text"]["text"]) <= MAX_TEXT_LENGTH_SECTION
        assert b["text"]["text"].endswith("...")

    def test_plain_text_type(self):
        b = section_block("plain", text_type="plain_text")
        assert b["text"]["type"] == "plain_text"


class TestHeaderBlock:
    def test_basic_header(self):
        b = header_block("Title")
        assert b["type"] == "header"
        assert b["text"]["type"] == "plain_text"
        assert b["text"]["text"] == "Title"

    def test_truncation(self):
        b = header_block("x" * 200)
        assert len(b["text"]["text"]) <= 150


class TestContextBlock:
    def test_basic(self):
        b = context_block([{"type": "mrkdwn", "text": "info"}])
        assert b["type"] == "context"
        assert len(b["elements"]) == 1

    def test_block_id(self):
        b = context_block([], block_id="ctx")
        assert b["block_id"] == "ctx"


class TestDividerBlock:
    def test_basic(self):
        b = divider_block()
        assert b["type"] == "divider"


class TestActionsBlock:
    def test_basic(self):
        btn = button_element("Click", "act")
        b = actions_block([btn], block_id="act_block")
        assert b["type"] == "actions"
        assert len(b["elements"]) == 1
        assert b["block_id"] == "act_block"


# ===========================================================================
# Interactive element builders
# ===========================================================================


class TestButtonElement:
    def test_basic(self):
        e = button_element("Approve", "hermes_approve")
        assert e["type"] == "button"
        assert e["text"]["text"] == "Approve"
        assert e["action_id"] == "hermes_approve"
        assert "value" not in e
        assert "style" not in e

    def test_with_value_and_style(self):
        e = button_element("Deny", "deny", value="session-key", style="danger")
        assert e["value"] == "session-key"
        assert e["style"] == "danger"

    def test_truncation(self):
        e = button_element("x" * 100, "act")
        assert len(e["text"]["text"]) <= MAX_TEXT_LENGTH_BUTTON

    def test_confirm_dialog(self):
        cd = confirmation_dialog("Sure?", "Are you sure?")
        e = button_element("Do it", "act", confirm=cd)
        assert e["confirm"]["title"]["text"] == "Sure?"


class TestStaticSelectElement:
    def test_basic(self):
        opts = [option_object("A", "a"), option_object("B", "b")]
        e = static_select_element("select_act", opts, placeholder="Pick one")
        assert e["type"] == "static_select"
        assert e["action_id"] == "select_act"
        assert len(e["options"]) == 2
        assert e["placeholder"]["text"] == "Pick one"

    def test_initial_option(self):
        opts = [option_object("A", "a"), option_object("B", "b")]
        e = static_select_element("sel", opts, initial_option=opts[1])
        assert e["initial_option"]["value"] == "b"

    def test_options_limit(self):
        opts = [option_object(f"opt{i}", f"v{i}") for i in range(200)]
        e = static_select_element("sel", opts)
        assert len(e["options"]) <= MAX_OPTIONS_SELECT


class TestOptionObject:
    def test_basic(self):
        o = option_object("Label", "val")
        assert o["text"]["type"] == "plain_text"
        assert o["text"]["text"] == "Label"
        assert o["value"] == "val"


class TestOverflowElement:
    def test_basic(self):
        opts = [option_object("A", "a")]
        e = overflow_element("overflow_act", opts)
        assert e["type"] == "overflow"
        assert len(e["options"]) == 1


class TestConfirmationDialog:
    def test_defaults(self):
        cd = confirmation_dialog("Title", "Body text")
        assert cd["title"]["text"] == "Title"
        assert cd["text"]["text"] == "Body text"
        assert cd["confirm"]["text"] == "Confirm"
        assert cd["deny"]["text"] == "Cancel"


# ===========================================================================
# Composite block builders (Hermes-specific)
# ===========================================================================


class TestBuildApprovalBlocks:
    def test_basic_approval(self):
        blocks = build_approval_blocks("rm -rf /", "session:123")
        assert len(blocks) == 2
        assert blocks[0]["type"] == "section"
        assert "rm -rf /" in blocks[0]["text"]["text"]
        assert blocks[1]["type"] == "actions"
        elements = blocks[1]["elements"]
        assert len(elements) == 4
        action_ids = [e["action_id"] for e in elements]
        assert "hermes_approve_once" in action_ids
        assert "hermes_approve_session" in action_ids
        assert "hermes_approve_always" in action_ids
        assert "hermes_deny" in action_ids

    def test_dangerous_has_confirm(self):
        blocks = build_approval_blocks("rm -rf /", "s", dangerous=True)
        deny_btn = [e for e in blocks[1]["elements"] if e["action_id"] == "hermes_deny"][0]
        assert "confirm" in deny_btn
        assert deny_btn["confirm"]["title"]["text"] == "Deny command?"

    def test_not_dangerous_no_confirm(self):
        blocks = build_approval_blocks("ls", "s", dangerous=False)
        deny_btn = [e for e in blocks[1]["elements"] if e["action_id"] == "hermes_deny"][0]
        assert "confirm" not in deny_btn

    def test_button_values_carry_session_key(self):
        blocks = build_approval_blocks("cmd", "my-session-key")
        for e in blocks[1]["elements"]:
            assert e["value"] == "my-session-key"


class TestBuildResolvedApprovalBlocks:
    def test_basic(self):
        blocks = build_resolved_approval_blocks("original", "Approved by alice")
        assert len(blocks) == 2
        assert blocks[0]["type"] == "section"
        assert blocks[1]["type"] == "context"
        assert "Approved by alice" in blocks[1]["elements"][0]["text"]

    def test_empty_original(self):
        blocks = build_resolved_approval_blocks("", "Decided")
        assert blocks[0]["text"]["text"] == "Command approval request"


class TestBuildSlashConfirmBlocks:
    def test_basic(self):
        blocks = build_slash_confirm_blocks("Deploy?", "Are you sure?", "sess", "cid")
        assert len(blocks) == 2
        assert blocks[0]["type"] == "section"
        assert blocks[1]["type"] == "actions"
        elements = blocks[1]["elements"]
        assert len(elements) == 3
        action_ids = [e["action_id"] for e in elements]
        assert "hermes_confirm_once" in action_ids
        assert "hermes_confirm_always" in action_ids
        assert "hermes_confirm_cancel" in action_ids

    def test_button_values_encode_session_and_confirm(self):
        blocks = build_slash_confirm_blocks("T", "B", "sess", "cid")
        for e in blocks[1]["elements"]:
            assert e["value"] == "sess|cid"


class TestBuildResolvedConfirmBlocks:
    def test_basic(self):
        blocks = build_resolved_confirm_blocks("prompt text", "Confirmed by bob")
        assert len(blocks) == 2
        assert blocks[0]["type"] == "section"
        assert blocks[1]["type"] == "context"


class TestBuildClarifyBlocks:
    def test_few_choices(self):
        blocks = build_clarify_blocks("Which one?", ["A", "B", "C"], "cl1")
        assert len(blocks) == 2
        assert blocks[0]["type"] == "section"
        assert blocks[1]["type"] == "actions"
        # 3 choice buttons + "Other" button
        assert len(blocks[1]["elements"]) == 4

    def test_five_choices(self):
        blocks = build_clarify_blocks("Pick", ["A", "B", "C", "D", "E"], "cl2")
        assert len(blocks[1]["elements"]) == 6  # 5 + Other

    def test_many_choices_fallback(self):
        choices = [f"Option {i}" for i in range(10)]
        blocks = build_clarify_blocks("Pick one", choices, "cl3")
        # Should fall back to numbered list + single "Other" button
        assert len(blocks) == 2
        section_text = blocks[0]["text"]["text"]
        assert "1." in section_text  # numbered list
        assert len(blocks[1]["elements"]) == 1  # just "Other"

    def test_button_values_encode_id_and_index(self):
        blocks = build_clarify_blocks("Q", ["Alpha", "Beta"], "cid99")
        elements = blocks[1]["elements"]
        # First choice: "cid99:0"
        assert elements[0]["value"] == "cid99:0"
        # Second choice: "cid99:1"
        assert elements[1]["value"] == "cid99:1"
        # Other button: just the clarify_id
        assert elements[2]["value"] == "cid99"

    def test_question_truncation(self):
        long_q = "x" * 4000
        blocks = build_clarify_blocks(long_q, ["A"], "cl")
        assert len(blocks[0]["text"]["text"]) <= 3000


class TestBuildStatusFooter:
    def test_full_footer(self):
        f = build_status_footer(
            model="claude-sonnet-4",
            provider="anthropic",
            tokens_in=1000,
            tokens_out=500,
            duration_s=12.3,
            session_id="abc123def456",
        )
        assert f["type"] == "context"
        text = f["elements"][0]["text"]
        assert "claude-sonnet-4" in text
        assert "anthropic" in text
        assert "1,000→500" in text
        assert "12.3s" in text
        assert "abc123de" in text  # truncated to 8 chars

    def test_empty_footer(self):
        f = build_status_footer()
        assert f["type"] == "context"
        assert "Hermes" in f["elements"][0]["text"]

    def test_milliseconds(self):
        f = build_status_footer(duration_s=0.5)
        assert "500ms" in f["elements"][0]["text"]

    def test_minutes_seconds(self):
        f = build_status_footer(duration_s=125)
        text = f["elements"][0]["text"]
        assert "2m05s" in text

    def test_output_tokens_only(self):
        f = build_status_footer(tokens_out=300)
        text = f["elements"][0]["text"]
        assert "300" in text


class TestBuildThinkingToggleBlock:
    def test_enabled_state(self):
        b = build_thinking_toggle_block("sess1", currently_enabled=True)
        assert b["type"] == "actions"
        btn = b["elements"][0]
        assert "Hide" in btn["text"]["text"]
        assert btn["value"] == "sess1:off"

    def test_disabled_state(self):
        b = build_thinking_toggle_block("sess1", currently_enabled=False)
        btn = b["elements"][0]
        assert "Show" in btn["text"]["text"]
        assert btn["value"] == "sess1:on"

    def test_block_id(self):
        b = build_thinking_toggle_block("sk", currently_enabled=True)
        assert b["block_id"] == "hermes_thinking_sk"


class TestBuildThinkingRevealBlocks:
    def test_basic(self):
        blocks = build_thinking_reveal_blocks("I think therefore I am", "sess1")
        assert len(blocks) == 2
        assert blocks[0]["type"] == "section"
        assert "I think therefore I am" in blocks[0]["text"]["text"]
        assert blocks[1]["type"] == "actions"

    def test_truncation(self):
        long_text = "x" * 5000
        blocks = build_thinking_reveal_blocks(long_text, "s")
        section_text = blocks[0]["text"]["text"]
        assert len(section_text) < 5000
        assert section_text.endswith("...")


class TestBuildThinkingCollapsedBlock:
    def test_returns_toggle(self):
        b = build_thinking_collapsed_block("summary", "sess1")
        assert b["type"] == "actions"
        btn = b["elements"][0]
        assert "Show" in btn["text"]["text"]


class TestBuildModelSelectorBlock:
    def test_basic(self):
        models = [
            {"label": "Claude Sonnet 4", "value": "anthropic/claude-sonnet-4"},
            {"label": "GPT-5", "value": "openai/gpt-5"},
        ]
        b = build_model_selector_block("sess1", models)
        assert b["type"] == "actions"
        select = b["elements"][0]
        assert select["type"] == "static_select"
        assert select["action_id"] == "hermes_model_select"
        assert len(select["options"]) == 2

    def test_initial_option(self):
        models = [
            {"label": "A", "value": "a"},
            {"label": "B", "value": "b"},
        ]
        b = build_model_selector_block("s", models, current_model="b")
        select = b["elements"][0]
        assert select["initial_option"]["value"] == "b"

    def test_no_initial(self):
        b = build_model_selector_block("s", [{"label": "A", "value": "a"}])
        select = b["elements"][0]
        assert "initial_option" not in select


class TestBuildReasoningLevelBlock:
    def test_defaults(self):
        b = build_reasoning_level_block("sess1")
        select = b["elements"][0]
        assert select["type"] == "static_select"
        assert select["action_id"] == "hermes_reasoning_select"
        assert len(select["options"]) == 6  # none, minimal, low, medium, high, xhigh

    def test_custom_levels(self):
        b = build_reasoning_level_block("s", ["low", "high"])
        select = b["elements"][0]
        assert len(select["options"]) == 2

    def test_initial_level(self):
        b = build_reasoning_level_block("s", current_level="high")
        select = b["elements"][0]
        assert select["initial_option"]["value"] == "reasoning:high"


class TestBuildToolProgressBlocks:
    def test_empty(self):
        assert build_tool_progress_blocks([]) == []

    def test_running(self):
        blocks = build_tool_progress_blocks(["tool: run tests"], running=True)
        assert len(blocks) == 2
        assert "⏳" in blocks[1]["elements"][0]["text"]

    def test_complete(self):
        blocks = build_tool_progress_blocks(["tool: done"], running=False)
        assert "✅" in blocks[1]["elements"][0]["text"]

    def test_truncation(self):
        lines = ["x" * 1000 for _ in range(10)]
        blocks = build_tool_progress_blocks(lines, max_chars=500)
        text = blocks[0]["text"]["text"]
        assert len(text) <= 500


class TestBuildSessionControlsBlock:
    def test_empty(self):
        blocks = build_session_controls_block("s")
        assert blocks == []

    def test_model_selector_only(self):
        models = [{"label": "A", "value": "a"}]
        blocks = build_session_controls_block("s", include_model_select=True, models=models)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "actions"

    def test_reasoning_only(self):
        blocks = build_session_controls_block("s", include_reasoning=True, current_reasoning="low")
        assert len(blocks) == 1

    def test_both(self):
        models = [{"label": "A", "value": "a"}]
        blocks = build_session_controls_block(
            "s", include_model_select=True, models=models, include_reasoning=True
        )
        assert len(blocks) == 1
        # Two elements in the actions block
        assert len(blocks[0]["elements"]) == 2


# ===========================================================================
# markdown_to_blocks converter
# ===========================================================================


class TestMarkdownToBlocks:
    def test_empty(self):
        assert markdown_to_blocks("") == []

    def test_plain_text(self):
        blocks = markdown_to_blocks("Hello world")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "section"
        assert blocks[0]["text"]["text"] == "Hello world"

    def test_single_header(self):
        blocks = markdown_to_blocks("## Title\nSome content")
        assert len(blocks) == 1
        # H2 becomes bold
        assert "*Title*" in blocks[0]["text"]["text"]

    def test_multiple_headers_produce_dividers(self):
        md = "## Section 1\nContent 1\n\n## Section 2\nContent 2"
        blocks = markdown_to_blocks(md)
        # 2 section blocks + 1 divider between them
        section_blocks = [b for b in blocks if b["type"] == "section"]
        divider_blocks = [b for b in blocks if b["type"] == "divider"]
        assert len(section_blocks) == 2
        assert len(divider_blocks) == 1

    def test_h3_becomes_italic(self):
        blocks = markdown_to_blocks("### Subtitle\nText")
        assert "_Subtitle_" in blocks[0]["text"]["text"]

    def test_block_limit(self):
        # Generate content that would produce >50 blocks
        md = "\n\n".join(f"## Section {i}\nContent" for i in range(60))
        blocks = markdown_to_blocks(md)
        # Sections + dividers + footer <= 50
        assert len(blocks) <= MAX_BLOCKS_PER_MESSAGE

    def test_status_footer(self):
        blocks = markdown_to_blocks(
            "Hello",
            include_status_footer=True,
            status_model="gpt-5",
            status_provider="openai",
            status_tokens_in=100,
            status_tokens_out=50,
            status_duration_s=2.0,
        )
        # Last block should be a context footer
        assert blocks[-1]["type"] == "context"
        assert "gpt-5" in blocks[-1]["elements"][0]["text"]

    def test_text_truncation(self):
        long_content = "x" * 10000
        blocks = markdown_to_blocks(long_content)
        assert len(blocks[0]["text"]["text"]) <= MAX_TEXT_LENGTH_SECTION


# ===========================================================================
# Constants and edge cases
# ===========================================================================


class TestConstants:
    def test_max_blocks(self):
        assert MAX_BLOCKS_PER_MESSAGE == 50

    def test_max_options(self):
        assert MAX_OPTIONS_SELECT == 100
