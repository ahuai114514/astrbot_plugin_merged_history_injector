from __future__ import annotations

from typing import Any


class ForwardHistoryBuilder:
    def __init__(self, max_depth: int, max_nodes: int, max_chars: int, header_text: str) -> None:
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.max_chars = max_chars
        self.header_text = header_text.strip()
        self.bot_self_id = ""

    def build_contexts(self, payloads: list[Any], fallback_text: str, bot_self_id: str | None = None) -> list[dict[str, Any]]:
        self.bot_self_id = str(bot_self_id).strip() if bot_self_id is not None else ""
        contexts: list[dict[str, Any]] = []
        if self.header_text:
            contexts.append(self._context_item("user", self.header_text))

        for payload in payloads:
            contexts.extend(self._contexts_from_payload(payload, depth=0))

        if contexts:
            return contexts

        fallback = fallback_text.strip()
        if fallback:
            contexts.append(self._context_item("user", self._truncate(fallback)))
        return contexts

    def _contexts_from_payload(self, payload: Any, depth: int) -> list[dict[str, Any]]:
        if depth > self.max_depth:
            return [self._context_item("user", f"[嵌套层数超过限制，已在 depth={depth} 截断]")]

        entries = self._extract_entries(payload)
        if entries:
            return self._contexts_from_entries(entries, depth)

        results: list[dict[str, Any]] = []
        for child in self._iter_nested(payload):
            results.extend(self._contexts_from_payload(child, depth + 1))
        return results

    def _contexts_from_entries(self, entries: list[Any], depth: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if depth > 0:
            results.append(self._context_item("user", f"[嵌套转发 depth={depth}]"))

        for index, entry in enumerate(entries):
            if index >= self.max_nodes:
                results.append(self._context_item("user", "[节点过多，已截断]"))
                break

            sender = self._sender_name(entry) or "未知发送者"
            sender_id = self._sender_id(entry)
            role = self._resolve_role(sender_id)
            body_parts = self._text_parts(entry)

            nested_items: list[dict[str, Any]] = []
            for child in self._iter_nested(entry):
                nested_items.extend(self._contexts_from_payload(child, depth + 1))

            text = " ".join(part for part in body_parts if part.strip()).strip()
            if text:
                results.append(self._context_item(role, self._truncate(f"[{sender}] {text}")))
            elif not nested_items:
                results.append(self._context_item(role, f"[{sender}] [非文本节点]"))

            results.extend(nested_items)
        return results

    def _extract_entries(self, value: Any) -> list[Any]:
        if isinstance(value, dict):
            for key in ("messages", "msgs", "data", "records"):
                item = value.get(key)
                if isinstance(item, list) and item:
                    return item
            message_item = value.get("message")
            if isinstance(message_item, list) and message_item:
                return message_item
        if isinstance(value, list) and value and any(isinstance(item, dict) for item in value):
            return value
        return []

    def _iter_nested(self, value: Any) -> list[Any]:
        nested: list[Any] = []

        if isinstance(value, dict):
            if self._looks_like_component(value):
                data = value.get("data", {})
                if value.get("type") == "forward" and isinstance(data, dict):
                    content = data.get("content")
                    if isinstance(content, list):
                        nested.append(content)
                return nested

            for key in ("messages", "msgs", "data", "records", "children"):
                item = value.get(key)
                if item is not None and not isinstance(item, (str, int, float, bool)):
                    nested.append(item)
        return nested

    def _sender_name(self, entry: Any) -> str:
        if not isinstance(entry, dict):
            return ""

        for key in ("nickname", "name", "user_name"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        sender = entry.get("sender")
        if isinstance(sender, dict):
            for key in ("nickname", "name", "card", "user_name", "user_id"):
                value = sender.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, int):
                    return str(value)
        return ""

    def _sender_id(self, entry: Any) -> str:
        if not isinstance(entry, dict):
            return ""

        for key in ("user_id", "uin", "sender_id"):
            value = entry.get(key)
            if value is not None:
                return str(value).strip()

        sender = entry.get("sender")
        if isinstance(sender, dict):
            for key in ("user_id", "uin", "id"):
                value = sender.get(key)
                if value is not None:
                    return str(value).strip()
        return ""

    def _resolve_role(self, sender_id: str) -> str:
        if sender_id and self.bot_self_id and sender_id == self.bot_self_id:
            return "assistant"
        return "user"

    def _text_parts(self, entry: Any) -> list[str]:
        if isinstance(entry, str):
            text = entry.strip()
            return [text] if text else []
        if not isinstance(entry, dict):
            return []

        if self._looks_like_component(entry):
            part = self._component_to_text(entry)
            return [part] if part else []

        texts: list[str] = []
        for key in ("text", "content", "message"):
            texts.extend(self._collect_plain_texts(entry.get(key)))

        for key in ("message", "content"):
            item = entry.get(key)
            if isinstance(item, list):
                for component in item:
                    if not isinstance(component, dict):
                        continue
                    comp_type = component.get("type")
                    data = component.get("data")
                    if comp_type == "text" and isinstance(data, dict):
                        text = data.get("text")
                        if isinstance(text, str) and text.strip():
                            texts.append(text.strip())
                    elif comp_type == "image":
                        texts.append("[图片]")
                    elif comp_type in ("file", "record", "video"):
                        texts.append(f"[{comp_type}]")
        return [text for text in texts if text.strip()]

    def _collect_plain_texts(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, dict):
            texts: list[str] = []
            for key in ("text", "content", "message"):
                item = value.get(key)
                if isinstance(item, str) and item.strip():
                    texts.append(item.strip())
            return texts
        if isinstance(value, list):
            texts: list[str] = []
            for item in value:
                texts.extend(self._collect_plain_texts(item))
            return texts
        text = getattr(value, "text", None)
        if isinstance(text, str) and text.strip():
            return [text.strip()]
        return []

    def _looks_like_component(self, value: Any) -> bool:
        return isinstance(value, dict) and isinstance(value.get("type"), str) and isinstance(value.get("data"), dict)

    def _component_to_text(self, component: dict[str, Any]) -> str:
        comp_type = component.get("type")
        data = component.get("data", {})
        if comp_type == "text":
            text = data.get("text")
            return text.strip() if isinstance(text, str) else ""
        if comp_type == "at":
            qq = data.get("qq")
            return f"[at:{qq}]" if qq else "[at]"
        if comp_type == "image":
            return "[图片]"
        if comp_type in ("file", "record", "video", "face", "reply"):
            return f"[{comp_type}]"
        return ""

    def _context_item(self, role: str, text: str) -> dict[str, Any]:
        return {
            "role": role,
            "content": [
                {
                    "type": "text",
                    "text": self._truncate(text),
                }
            ],
        }

    def _truncate(self, text: str) -> str:
        if len(text) > self.max_chars:
            return text[: self.max_chars - 12].rstrip() + "...[已截断]"
        return text
