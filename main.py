from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

try:
    from .forward_resolver import ForwardResolver
    from .history_builder import ForwardHistoryBuilder
except ImportError:
    from forward_resolver import ForwardResolver
    from history_builder import ForwardHistoryBuilder


PLUGIN_ID = "astrbot_plugin_merged_history_injector"
DEFAULT_PROCESSING_MESSAGE = "已导入引用聊天记录"
DEFAULT_HEADER_TEXT = ""


@register(PLUGIN_ID, "Codex", "将引用的 QQ 合并转发记录注入为 contexts 历史", "1.0.0")
class MergedHistoryInjectorPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config

    @filter.regex(r"^[\s\S]*$")
    async def handle_merged_history(self, event: AstrMessageEvent):
        if not self._is_enabled():
            return

        resolver = self._forward_resolver()
        await resolver.debug_event_shapes(event)
        if not resolver.should_process_event(event):
            return

        quoted = resolver.extract_quoted_message(event)
        if quoted is None:
            return

        payloads = await resolver.fetch_forward_payloads(event, quoted)
        contexts = self._history_builder().build_contexts(
            payloads,
            "",
            bot_self_id=resolver.extract_bot_self_id(event),
        )
        if not contexts:
            contexts = self._history_builder().build_contexts(
                payloads=[],
                fallback_text=resolver.render_forward_bundle(quoted),
                bot_self_id=resolver.extract_bot_self_id(event),
            )
        if not contexts:
            yield event.plain_result("我看到你引用了转发记录，但暂时没能整理出可注入的历史内容。")
            return

        await self._persist_contexts(event, contexts)

        event.stop_event()
        processing_message = self._processing_message()
        if processing_message:
            await event.send(event.plain_result(processing_message))
        return

    async def _persist_contexts(self, event: AstrMessageEvent, contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        conversation_manager = getattr(self.context, "conversation_manager", None)
        if conversation_manager is None:
            return contexts

        umo = event.unified_msg_origin
        curr_cid = await conversation_manager.get_curr_conversation_id(umo)
        conversation = None
        existing_history: list[dict[str, Any]] = []

        if curr_cid:
            conversation = await conversation_manager.get_conversation(umo, curr_cid)
            if conversation and getattr(conversation, "history", None):
                try:
                    existing_history = json.loads(conversation.history or "[]")
                except json.JSONDecodeError:
                    existing_history = []

        if not curr_cid or not conversation:
            curr_cid = await conversation_manager.new_conversation(umo)

        merged_history = self._append_unique_contexts(existing_history, contexts)
        if merged_history == existing_history:
            if self._debug_enabled():
                logger.info("[%s] persisted_contexts skipped total=%s", PLUGIN_ID, len(existing_history))
            return existing_history
        await conversation_manager.update_conversation(umo, curr_cid, history=merged_history)

        if self._debug_enabled():
            logger.info(
                "[%s] persisted_contexts appended=%s total=%s",
                PLUGIN_ID,
                max(0, len(merged_history) - len(existing_history)),
                len(merged_history),
            )

        return merged_history

    def _append_unique_contexts(
        self,
        existing_history: list[dict[str, Any]],
        contexts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not existing_history:
            return list(contexts)
        if not contexts:
            return list(existing_history)

        if self._contains_context_block(existing_history, contexts):
            return list(existing_history)
        return list(existing_history) + list(contexts)

    def _contains_context_block(
        self,
        haystack: list[dict[str, Any]],
        needle: list[dict[str, Any]],
    ) -> bool:
        if not needle:
            return True
        if len(needle) > len(haystack):
            return False

        needle_key = [self._context_signature(item) for item in needle]
        haystack_key = [self._context_signature(item) for item in haystack]
        width = len(needle_key)
        for index in range(len(haystack_key) - width + 1):
            if haystack_key[index : index + width] == needle_key:
                return True
        return False

    def _context_signature(self, item: dict[str, Any]) -> str:
        try:
            return json.dumps(item, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return repr(item)

    def _forward_resolver(self) -> ForwardResolver:
        return ForwardResolver(
            plugin_id=PLUGIN_ID,
            max_depth=self._max_depth(),
            max_nodes=self._max_nodes(),
            max_chars=self._max_chars(),
            debug_enabled=self._debug_enabled(),
        )

    def _history_builder(self) -> ForwardHistoryBuilder:
        return ForwardHistoryBuilder(
            max_depth=self._max_depth(),
            max_nodes=self._max_nodes(),
            max_chars=self._max_chars(),
            header_text=self._header_text(),
        )

    def _is_enabled(self) -> bool:
        return bool(self._config_get("enabled", True))

    def _debug_enabled(self) -> bool:
        return bool(self._config_get("debug_logging", False))

    def _processing_message(self) -> str:
        value = self._config_get("processing_message", DEFAULT_PROCESSING_MESSAGE)
        return value.strip() if isinstance(value, str) else DEFAULT_PROCESSING_MESSAGE

    def _header_text(self) -> str:
        value = self._config_get("header_text", DEFAULT_HEADER_TEXT)
        return value.strip() if isinstance(value, str) else ""

    def _max_depth(self) -> int:
        return self._safe_int("max_depth", 4, minimum=1)

    def _max_nodes(self) -> int:
        return self._safe_int("max_nodes", 80, minimum=1)

    def _max_chars(self) -> int:
        return self._safe_int("max_chars", 12000, minimum=200)

    def _safe_int(self, key: str, default: int, minimum: int) -> int:
        raw = self._config_get(key, default)
        try:
            return max(minimum, int(raw))
        except (TypeError, ValueError):
            return default

    def _config_get(self, key: str, default: Any) -> Any:
        if self.config is None:
            return default
        if hasattr(self.config, "get"):
            return self.config.get(key, default)
        return getattr(self.config, key, default)
