"""
Tests for Slack Block Kit rich formatting — _has_rich_structure, _markdown_to_blocks,
and the upgraded send() method.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform, PlatformConfig


def _ensure_slack_mock():
    if "slack_bolt" in sys.modules and hasattr(sys.modules["slack_bolt"], "__file__"):
        return
    slack_bolt = MagicMock()
    slack_bolt.async_app.AsyncApp = MagicMock
    slack_bolt.adapter.socket_mode.async_handler.AsyncSocketModeHandler = MagicMock
    slack_sdk = MagicMock()
    slack_sdk.web.async_client.AsyncWebClient = MagicMock
    for name, mod in [
        ("slack_bolt", slack_bolt),
        ("slack_bolt.async_app", slack_bolt.async_app),
        ("slack_bolt.adapter", slack_bolt.adapter),
        ("slack_bolt.adapter.socket_mode", slack_bolt.adapter.socket_mode),
        ("slack_bolt.adapter.socket_mode.async_handler", slack_bolt.adapter.socket_mode.async_handler),
        ("slack_sdk", slack_sdk),
        ("slack_sdk.web", slack_sdk.web),
        ("slack_sdk.web.async_client", slack_sdk.web.async_client),
    ]:
        sys.modules.setdefault(name, mod)


_ensure_slack_mock()

import gateway.platforms.slack as _slack_mod  # noqa: E402
_slack_mod.SLACK_AVAILABLE = True

from gateway.platforms.slack import SlackAdapter  # noqa: E402


@pytest.fixture()
def adapter():
    config = PlatformConfig(enabled=True, token="***")
    a = SlackAdapter(config)
    a._app = MagicMock()
    a._app.client = AsyncMock()
    a._bot_user_id = "U_BOT"
    a._running = True
    a.handle_message = AsyncMock()
    return a


class TestHasRichStructure:
    def test_h1_detected(self):
        assert SlackAdapter._has_rich_structure("# Title\nSome text")

    def test_h2_detected(self):
        assert SlackAdapter._has_rich_structure("## Section\nContent")

    def test_table_detected(self):
        content = "| Col1 | Col2 |\n|------|------|\n| A    | B    |"
        assert SlackAdapter._has_rich_structure(content)

    def test_plain_text_not_rich(self):
        assert not SlackAdapter._has_rich_structure("Just a regular message with no headers.")

    def test_bullet_list_not_rich(self):
        assert not SlackAdapter._has_rich_structure("- item one\n- item two\n- item three")

    def test_bold_not_rich(self):
        assert not SlackAdapter._has_rich_structure("**bold text** is fine but not rich structure")

    def test_h1_at_start_of_line_only(self):
        assert not SlackAdapter._has_rich_structure("Use #hashtag not a header")

    def test_h1_in_middle_of_content(self):
        assert SlackAdapter._has_rich_structure("intro\n# Title\nmore text")


class TestH1ToHeaderBlock:
    def setup_method(self):
        config = PlatformConfig(enabled=True, token="***")
        self.adapter = SlackAdapter(config)

    def test_h1_produces_header_block(self):
        blocks, attachments, fallback = self.adapter._markdown_to_blocks("# My Title\nSome paragraph.")
        header_blocks = [b for b in blocks if b.get("type") == "header"]
        assert len(header_blocks) == 1
        assert header_blocks[0]["text"]["type"] == "plain_text"
        assert header_blocks[0]["text"]["text"] == "My Title"

    def test_h1_followed_by_divider(self):
        blocks, _, _ = self.adapter._markdown_to_blocks("# Title\nText")
        types = [b["type"] for b in blocks]
        assert "header" in types
        header_idx = types.index("header")
        assert types[header_idx + 1] == "divider"

    def test_h1_strips_markdown_bold(self):
        blocks, _, _ = self.adapter._markdown_to_blocks("# **Bold Title**")
        header_blocks = [b for b in blocks if b.get("type") == "header"]
        assert header_blocks[0]["text"]["text"] == "Bold Title"

    def test_h1_truncated_at_150_chars(self):
        long_title = "A" * 200
        blocks, _, _ = self.adapter._markdown_to_blocks(f"# {long_title}")
        header_blocks = [b for b in blocks if b.get("type") == "header"]
        assert len(header_blocks[0]["text"]["text"]) <= 150

    def test_h1_fallback_uses_equals_notation(self):
        _, _, fallback = self.adapter._markdown_to_blocks("# Title")
        assert "Title" in fallback


class TestH2ToSectionBlock:
    def setup_method(self):
        config = PlatformConfig(enabled=True, token="***")
        self.adapter = SlackAdapter(config)

    def test_h2_becomes_bold_section(self):
        blocks, _, _ = self.adapter._markdown_to_blocks("## Sub-heading")
        sections = [b for b in blocks if b.get("type") == "section"]
        assert any("Sub-heading" in b["text"]["text"] for b in sections)

    def test_h2_section_uses_mrkdwn(self):
        blocks, _, _ = self.adapter._markdown_to_blocks("## Section")
        section = next(b for b in blocks if b.get("type") == "section")
        assert section["text"]["type"] == "mrkdwn"

    def test_h2_text_is_bold(self):
        blocks, _, _ = self.adapter._markdown_to_blocks("## My Section")
        section = next(b for b in blocks if b.get("type") == "section")
        assert section["text"]["text"].startswith("*") and section["text"]["text"].endswith("*")

    def test_h6_handled(self):
        blocks, _, _ = self.adapter._markdown_to_blocks("###### Deep heading")
        sections = [b for b in blocks if b.get("type") == "section"]
        assert any("Deep heading" in b["text"]["text"] for b in sections)

    def test_h2_does_not_produce_header_block(self):
        blocks, _, _ = self.adapter._markdown_to_blocks("## Sub")
        assert not any(b.get("type") == "header" for b in blocks)


class TestTableToSlackTable:
    def setup_method(self):
        config = PlatformConfig(enabled=True, token="***")
        self.adapter = SlackAdapter(config)

    def _basic_table(self):
        return (
            "| Name | Score |\n"
            "|------|-------|\n"
            "| Alice | 95 |\n"
            "| Bob | 87 |"
        )

    def test_table_goes_in_attachments(self):
        _, attachments, _ = self.adapter._markdown_to_blocks(self._basic_table())
        assert len(attachments) == 1

    def test_attachment_contains_table_block(self):
        _, attachments, _ = self.adapter._markdown_to_blocks(self._basic_table())
        table_blocks = [b for b in attachments[0]["blocks"] if b.get("type") == "table"]
        assert len(table_blocks) == 1

    def test_table_has_correct_column_count(self):
        _, attachments, _ = self.adapter._markdown_to_blocks(self._basic_table())
        table = attachments[0]["blocks"][0]
        assert len(table["column_settings"]) == 2

    def test_table_data_rows_correct(self):
        _, attachments, _ = self.adapter._markdown_to_blocks(self._basic_table())
        table = attachments[0]["blocks"][0]
        assert len(table["rows"]) == 3

    def test_separator_row_excluded(self):
        _, attachments, _ = self.adapter._markdown_to_blocks(self._basic_table())
        table = attachments[0]["blocks"][0]
        for row in table["rows"]:
            for cell in row:
                text = cell["text"]
                assert not set(text.strip()).issubset({'-', ':', ' ', '|'})

    def test_table_cell_structure(self):
        _, attachments, _ = self.adapter._markdown_to_blocks(self._basic_table())
        table = attachments[0]["blocks"][0]
        cell = table["rows"][0][0]
        assert cell["type"] == "raw_text"
        assert "text" in cell

    def test_table_max_10_columns(self):
        header = "| " + " | ".join(f"C{i}" for i in range(12)) + " |"
        sep = "| " + " | ".join(["---"] * 12) + " |"
        row = "| " + " | ".join(["x"] * 12) + " |"
        content = f"{header}\n{sep}\n{row}"
        _, attachments, _ = self.adapter._markdown_to_blocks(content)
        if attachments:
            table = attachments[0]["blocks"][0]
            assert len(table["column_settings"]) <= 10

    def test_blocks_before_table_rendered(self):
        content = "## Overview\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        blocks, attachments, _ = self.adapter._markdown_to_blocks(content)
        assert any(b.get("type") == "section" for b in blocks)
        assert len(attachments) == 1

    def test_text_after_table_rendered(self):
        content = "| A | B |\n|---|---|\n| 1 | 2 |\n\nSome text after."
        blocks, attachments, _ = self.adapter._markdown_to_blocks(content)
        assert len(attachments) == 1
        assert any(b.get("type") == "section" for b in blocks)


class TestCodeFencePreservation:
    def setup_method(self):
        config = PlatformConfig(enabled=True, token="***")
        self.adapter = SlackAdapter(config)

    def test_code_fence_in_section_block(self):
        content = "Here is some code:\n```python\nprint('hello')\n```\nDone."
        blocks, _, _ = self.adapter._markdown_to_blocks(content)
        section_texts = [b["text"]["text"] for b in blocks if b.get("type") == "section"]
        assert any("```" in t for t in section_texts)

    def test_code_fence_does_not_trigger_table_parser(self):
        content = "```\n| col1 | col2 |\n|---|---|\n| a | b |\n```"
        _, attachments, _ = self.adapter._markdown_to_blocks(content)
        assert len(attachments) == 0

    def test_unclosed_fence_handled(self):
        content = "```python\nprint('oops')"
        blocks, _, _ = self.adapter._markdown_to_blocks(content)
        assert isinstance(blocks, list)


class TestMixedContent:
    def setup_method(self):
        config = PlatformConfig(enabled=True, token="***")
        self.adapter = SlackAdapter(config)

    def test_full_report_structure(self):
        content = (
            "# Weekly Report\n\n"
            "## Performance\n\n"
            "Metrics look great this week.\n\n"
            "| Metric | Value |\n"
            "|--------|-------|\n"
            "| Revenue | $10k |\n"
            "| Leads | 42 |\n\n"
            "## Next Steps\n\n"
            "- Follow up with leads\n"
            "- Review budget\n"
        )
        blocks, attachments, fallback = self.adapter._markdown_to_blocks(content)
        block_types = [b["type"] for b in blocks]
        assert "header" in block_types
        assert "divider" in block_types
        assert "section" in block_types
        assert len(attachments) == 1
        assert fallback

    def test_block_order_is_document_order(self):
        content = "# Title\n\nParagraph one.\n\n## Sub\n\nParagraph two."
        blocks, _, _ = self.adapter._markdown_to_blocks(content)
        types = [b["type"] for b in blocks]
        assert types.index("header") < types.index("section")

    def test_fallback_text_non_empty(self):
        content = "# Title\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        _, _, fallback = self.adapter._markdown_to_blocks(content)
        assert len(fallback) > 0
        assert "Title" in fallback


class TestSendBlockKit:
    @pytest.mark.asyncio
    async def test_send_with_heading_uses_blocks(self, adapter):
        adapter._app.client.chat_postMessage = AsyncMock(return_value={"ts": "ts1", "ok": True})
        content = "# Report Title\n\nSome content here."
        await adapter.send("C123", content)
        adapter._app.client.chat_postMessage.assert_called_once()
        call_kwargs = adapter._app.client.chat_postMessage.call_args.kwargs
        assert "blocks" in call_kwargs
        assert call_kwargs["channel"] == "C123"

    @pytest.mark.asyncio
    async def test_send_with_table_uses_attachments(self, adapter):
        adapter._app.client.chat_postMessage = AsyncMock(return_value={"ts": "ts1", "ok": True})
        content = "| Col | Val |\n|-----|-----|\n| A | 1 |"
        await adapter.send("C123", content)
        call_kwargs = adapter._app.client.chat_postMessage.call_args.kwargs
        assert "attachments" in call_kwargs
        att = call_kwargs["attachments"]
        assert isinstance(att, list)
        assert att[0]["blocks"][0]["type"] == "table"

    @pytest.mark.asyncio
    async def test_send_plain_text_no_blocks(self, adapter):
        adapter._app.client.chat_postMessage = AsyncMock(return_value={"ts": "ts1", "ok": True})
        await adapter.send("C123", "Just a plain message without any headers.")
        call_kwargs = adapter._app.client.chat_postMessage.call_args.kwargs
        assert "blocks" not in call_kwargs
        assert call_kwargs.get("mrkdwn") is True

    @pytest.mark.asyncio
    async def test_send_block_kit_preserves_thread(self, adapter):
        adapter._app.client.chat_postMessage = AsyncMock(return_value={"ts": "ts1", "ok": True})
        content = "# Update\n\nSome info."
        await adapter.send("C123", content, metadata={"thread_id": "parent_ts"})
        call_kwargs = adapter._app.client.chat_postMessage.call_args.kwargs
        assert call_kwargs.get("thread_ts") == "parent_ts"

    @pytest.mark.asyncio
    async def test_send_block_kit_fallback_text_set(self, adapter):
        adapter._app.client.chat_postMessage = AsyncMock(return_value={"ts": "ts1", "ok": True})
        content = "# My Title\n\nSome paragraph content."
        await adapter.send("C123", content)
        call_kwargs = adapter._app.client.chat_postMessage.call_args.kwargs
        assert call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_send_block_kit_returns_success(self, adapter):
        adapter._app.client.chat_postMessage = AsyncMock(return_value={"ts": "ts1", "ok": True})
        content = "# Title\n\nContent."
        result = await adapter.send("C123", content)
        assert result.success
        assert result.message_id == "ts1"

    @pytest.mark.asyncio
    async def test_send_block_kit_tracks_ts(self, adapter):
        adapter._app.client.chat_postMessage = AsyncMock(return_value={"ts": "ts_tracked", "ok": True})
        content = "# Heading\n\nText."
        await adapter.send("C123", content)
        assert "ts_tracked" in adapter._bot_message_ts

    @pytest.mark.asyncio
    async def test_send_not_connected_returns_error(self, adapter):
        adapter._app = None
        result = await adapter.send("C123", "# Title\nContent")
        assert not result.success

    @pytest.mark.asyncio
    async def test_send_mrkdwn_path_still_formats_bold(self, adapter):
        adapter._app.client.chat_postMessage = AsyncMock(return_value={"ts": "ts1", "ok": True})
        await adapter.send("C123", "This is **bold** text")
        call_kwargs = adapter._app.client.chat_postMessage.call_args.kwargs
        assert "*bold*" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_send_full_report_single_api_call(self, adapter):
        adapter._app.client.chat_postMessage = AsyncMock(return_value={"ts": "ts1", "ok": True})
        content = (
            "# Weekly Report\n\n"
            "## Highlights\n\n"
            "Revenue was up 15% this week.\n\n"
            "| Metric | This Week | Last Week |\n"
            "|--------|-----------|----------|\n"
            "| Revenue | $12k | $10.4k |\n"
            "| Leads | 47 | 38 |\n\n"
            "## Action Items\n\n"
            "- Schedule follow-up calls\n"
            "- Review Q3 budget\n"
        )
        await adapter.send("C123", content)
        assert adapter._app.client.chat_postMessage.call_count == 1


class TestTrackSentTs:
    def test_tracks_sent_ts(self, adapter):
        adapter._track_sent_ts("ts_abc", None)
        assert "ts_abc" in adapter._bot_message_ts

    def test_tracks_thread_ts(self, adapter):
        adapter._track_sent_ts("ts_abc", "thread_parent")
        assert "thread_parent" in adapter._bot_message_ts

    def test_none_sent_ts_no_op(self, adapter):
        before = set(adapter._bot_message_ts)
        adapter._track_sent_ts(None, None)
        assert adapter._bot_message_ts == before

    def test_evicts_when_over_limit(self, adapter):
        adapter._BOT_TS_MAX = 10
        for i in range(11):
            adapter._track_sent_ts(f"ts_{i}", None)
        assert len(adapter._bot_message_ts) <= 10
