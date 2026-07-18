from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger


QUOTE_ATTRS = (
    "quote",
    "quoted",
    "reply_message",
    "reply",
    "source_message",
    "referenced_message",
)
MESSAGE_CHAIN_ATTRS = ("message", "message_chain", "chain", "messages", "components")
FORWARD_NODE_ATTRS = (
    "nodes",
    "node_list",
    "children",
    "messages",
    "content",
    "detail",
    "forward",
    "forwards",
    "merged",
    "merged_messages",
)
NESTED_ATTRS = (
    "quote",
    "quoted",
    "reply",
    "reply_message",
    "source_message",
    "referenced_message",
    "content",
    "message",
    "message_chain",
    "chain",
    "messages",
    "children",
    "nodes",
    "forward",
    "forwards",
    "merged",
    "merged_messages",
)
PAYLOAD_NESTED_KEYS = ("messages", "msgs", "data", "records", "children", "content", "message")


class ForwardResolver:
    def __init__(self, plugin_id: str, max_depth: int, max_nodes: int, max_chars: int, debug_enabled: bool) -> None:
        self.plugin_id = plugin_id
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.max_chars = max_chars
        self.debug_enabled = debug_enabled

    async def debug_event_shapes(self, event: Any) -> None:
        if not self.debug_enabled:
            return

        message_obj = getattr(event, "message_obj", None)
        raw_message = getattr(message_obj, "raw_message", None)
        chains = self.extract_message_chains(message_obj)
        quoted = self.extract_quoted_message(event)

        logger.info("[%s] debug message_str=%r", self.plugin_id, getattr(event, "message_str", ""))
        logger.info("[%s] debug chain_types=%s", self.plugin_id, [type(component).__name__ for component in chains])
        logger.info(
            "[%s] debug quoted_type=%s contains_forward=%s",
            self.plugin_id,
            type(quoted).__name__ if quoted is not None else None,
            self.contains_forward_content(quoted) if quoted is not None else False,
        )
        logger.info("[%s] debug quoted_repr=%r", self.plugin_id, quoted)
        logger.info("[%s] debug raw_message=%r", self.plugin_id, raw_message)

    def should_process_event(self, event: Any) -> bool:
        if not self.has_explicit_at_bot(event):
            return False
        quoted = self.extract_quoted_message(event)
        return quoted is not None and self.has_forward_component(quoted)

    def extract_quoted_message(self, event: Any) -> Any | None:
        message_obj = getattr(event, "message_obj", None)
        for name in QUOTE_ATTRS:
            value = getattr(message_obj, name, None)
            if value is not None:
                return value
        if isinstance(message_obj, dict):
            for name in QUOTE_ATTRS:
                value = message_obj.get(name)
                if value is not None:
                    return value
        for component in self.extract_message_chains(message_obj):
            if isinstance(component, Comp.Reply):
                return component
        return None

    async def fetch_forward_payloads(self, event: Any, root: Any) -> list[Any]:
        message_ids = self.extract_forward_message_ids(
            event,
            root,
            allow_event_raw_fallback=self.should_use_event_raw_fallback(root),
        )
        if self.debug_enabled:
            logger.info("[%s] debug forward_message_ids=%r", self.plugin_id, message_ids)

        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None)
        call_action = getattr(api, "call_action", None)
        if not callable(call_action):
            return []

        payloads: list[Any] = []
        for message_id in message_ids:
            try:
                payload = await call_action("get_forward_msg", message_id=message_id)
                payloads.append(payload)
                await self.debug_forward_payload(payload, event, message_id)
            except Exception as exc:
                if self.debug_enabled:
                    logger.info("[%s] debug get_forward_msg fail message_id=%r error=%r", self.plugin_id, message_id, exc)
        return payloads

    async def debug_forward_payload(self, payload: Any, event: Any, message_id: str) -> None:
        if not self.debug_enabled:
            return

        bot_self_id = self.extract_bot_self_id(event)
        entries = self.extract_debug_entries(payload)
        logger.info(
            "[%s] debug forward_payload_summary message_id=%r bot_self_id=%r entry_count=%s payload_type=%s",
            self.plugin_id,
            message_id,
            bot_self_id,
            len(entries),
            type(payload).__name__,
        )

        for index, entry in enumerate(entries[:5]):
            sender_name = self.debug_sender_name(entry)
            sender_ids = self.debug_sender_ids(entry)
            text_preview = self.debug_text_preview(entry)
            logger.info(
                "[%s] debug forward_entry[%s] sender_name=%r sender_ids=%r text_preview=%r raw=%r",
                self.plugin_id,
                index,
                sender_name,
                sender_ids,
                text_preview,
                entry,
            )

    def extract_bot_self_id(self, event: Any) -> str | None:
        for attr in ("self_id", "bot_id"):
            value = getattr(event, attr, None)
            if value is not None:
                return str(value)

        message_obj = getattr(event, "message_obj", None)
        for target in (message_obj, getattr(message_obj, "raw_message", None), event):
            if target is None:
                continue
            value = getattr(target, "self_id", None)
            if value is not None:
                return str(value)
            if isinstance(target, dict):
                dict_value = target.get("self_id")
                if dict_value is not None:
                    return str(dict_value)
        return None

    def extract_debug_entries(self, payload: Any) -> list[Any]:
        entries = self._extract_payload_entries(payload)
        if entries:
            return entries
        for child in self.iter_payload_nested_values(payload):
            nested_entries = self._extract_payload_entries(child)
            if nested_entries:
                return nested_entries
        return []

    def _extract_payload_entries(self, current: Any) -> list[Any]:
        if isinstance(current, dict):
            for key in ("messages", "msgs", "data", "records"):
                value = current.get(key)
                if isinstance(value, list) and value:
                    return value
            message_value = current.get("message")
            if isinstance(message_value, list) and message_value:
                return message_value
        if isinstance(current, list) and current and any(isinstance(item, dict) for item in current):
            return current
        return []

    def debug_sender_name(self, entry: Any) -> str:
        if not isinstance(entry, dict):
            return ""
        for key in ("nickname", "name", "user_name"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        sender = entry.get("sender")
        if isinstance(sender, dict):
            for key in ("nickname", "name", "card", "user_name"):
                value = sender.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    def debug_sender_ids(self, entry: Any) -> dict[str, str]:
        ids: dict[str, str] = {}
        if not isinstance(entry, dict):
            return ids

        for key in ("uin", "user_id", "sender_id"):
            value = entry.get(key)
            if value is not None:
                ids[key] = str(value)

        sender = entry.get("sender")
        if isinstance(sender, dict):
            for key in ("user_id", "uin", "id"):
                value = sender.get(key)
                if value is not None:
                    ids[f"sender.{key}"] = str(value)
        return ids

    def debug_text_preview(self, entry: Any) -> str:
        text = " ".join(self._payload_text_parts(entry, {"count": 0, "image_urls": []})).strip()
        return self.truncate_text(text[:200]) if text else ""

    def _payload_text_parts(self, entry: Any, image_state: dict[str, Any]) -> list[str]:
        if isinstance(entry, str):
            stripped = entry.strip()
            return [stripped] if stripped else []
        if not isinstance(entry, dict):
            return []

        texts: list[str] = []
        if self._looks_like_component_dict(entry):
            component_text = self._component_dict_to_text(entry, image_state)
            if component_text:
                texts.append(component_text)
            return [part for part in texts if isinstance(part, str) and part.strip()]

        for key in ("text", "content", "message"):
            value = entry.get(key)
            texts.extend(self._collect_plain_texts(value))

        for key in ("message", "content"):
            value = entry.get(key)
            if isinstance(value, list):
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    typ = item.get("type")
                    data = item.get("data")
                    if typ == "text" and isinstance(data, dict):
                        text = data.get("text")
                        if isinstance(text, str) and text.strip():
                            texts.append(text.strip())
                    elif typ == "image":
                        texts.append(self._register_image_placeholder(data, image_state))
                    elif typ in ("file", "record", "video"):
                        texts.append(f"[{typ}]")
        return [part for part in texts if isinstance(part, str) and part.strip()]

    def _collect_plain_texts(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        if isinstance(value, dict):
            texts: list[str] = []
            for key in ("text", "content", "message"):
                item = value.get(key)
                if isinstance(item, str) and item.strip():
                    texts.append(item.strip())
            return texts
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            texts: list[str] = []
            for item in value:
                texts.extend(self._collect_plain_texts(item))
            return texts
        text_attr = getattr(value, "text", None)
        if isinstance(text_attr, str) and text_attr.strip():
            return [text_attr.strip()]
        return []

    def _looks_like_component_dict(self, value: Any) -> bool:
        return isinstance(value, dict) and isinstance(value.get("type"), str) and isinstance(value.get("data"), dict)

    def _component_dict_to_text(self, component: dict[str, Any], image_state: dict[str, Any]) -> str:
        component_type = component.get("type")
        data = component.get("data", {})
        if component_type == "text":
            text = data.get("text")
            return text.strip() if isinstance(text, str) else ""
        if component_type == "at":
            qq = data.get("qq")
            return f"[at:{qq}]" if qq else "[at]"
        if component_type == "image":
            return self._register_image_placeholder(data, image_state)
        if component_type in ("file", "record", "video", "face", "reply"):
            return f"[{component_type}]"
        if component_type == "forward":
            return ""
        return f"[{component_type}]" if component_type else ""

    def _register_image_placeholder(self, data: Any, image_state: dict[str, Any]) -> str:
        image_state["count"] = int(image_state.get("count", 0)) + 1
        index = image_state["count"]
        image_url = self._extract_image_url(data)
        if image_url:
            image_state.setdefault("image_urls", []).append(image_url)
        return f"[图片{index}]"

    def _extract_image_url(self, data: Any) -> str | None:
        if not isinstance(data, dict):
            return None
        for key in ("url", "src", "image_url"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        for key in ("file", "path"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                if text.startswith(("http://", "https://", "file://")):
                    return text
                path = Path(text)
                if path.is_absolute():
                    return path.as_uri()
        return None

    def render_forward_bundle(self, root: Any) -> str:
        blocks = self.render_forward_blocks(root, depth=0, seen=set())
        lines = [block for block in blocks if block.strip()]
        return self.truncate_text("\n\n".join(lines)) if lines else ""

    def extract_forward_message_ids(self, event: Any, root: Any, allow_event_raw_fallback: bool = True) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()

        def push(value: Any) -> None:
            if value is None:
                return
            normalized = str(value).strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            ids.append(normalized)

        for item in self.iter_nested_values(root):
            if isinstance(item, Comp.Forward):
                push(getattr(item, "id", None))

        if isinstance(root, Comp.Reply):
            for component in getattr(root, "chain", []) or []:
                if isinstance(component, Comp.Forward):
                    push(getattr(component, "id", None))

        if allow_event_raw_fallback:
            raw_payload = self.raw_payload_from_event(event)
            if isinstance(raw_payload, dict):
                for element in raw_payload.get("elements", []) or []:
                    if not isinstance(element, dict):
                        continue
                    reply_element = element.get("replyElement")
                    if isinstance(reply_element, dict):
                        push(reply_element.get("sourceMsgIdInRecords"))
                        push(reply_element.get("replayMsgId"))
                for record in raw_payload.get("records", []) or []:
                    if isinstance(record, dict):
                        push(record.get("msgId"))

        return ids

    def should_use_event_raw_fallback(self, root: Any) -> bool:
        if isinstance(root, Comp.Reply):
            chain = getattr(root, "chain", None) or []
            if any(isinstance(component, Comp.Forward) for component in chain):
                return not self.extract_forward_message_ids_from_reply_chain(root)
        return False

    def extract_forward_message_ids_from_reply_chain(self, root: Any) -> list[str]:
        ids: list[str] = []
        if not isinstance(root, Comp.Reply):
            return ids
        for component in getattr(root, "chain", []) or []:
            if isinstance(component, Comp.Forward):
                forward_id = getattr(component, "id", None)
                if forward_id:
                    ids.append(str(forward_id).strip())
        return [item for item in ids if item]

    def raw_payload_from_event(self, event: Any) -> Any:
        message_obj = getattr(event, "message_obj", None)
        raw_message = getattr(message_obj, "raw_message", None)
        if isinstance(raw_message, dict):
            return raw_message.get("raw")
        return getattr(raw_message, "raw", None)

    def contains_forward_content(self, root: Any) -> bool:
        return self.has_forward_component(root)

    def has_forward_component(self, root: Any) -> bool:
        return self._has_forward_component(root, depth=0, seen=set())

    def _has_forward_component(self, current: Any, depth: int, seen: set[int]) -> bool:
        if current is None or depth > self.max_depth:
            return False

        marker = id(current)
        if marker in seen:
            return False
        seen.add(marker)

        if isinstance(current, Comp.Forward) or self.extract_forward_nodes(current):
            return True

        for child in self.iter_nested_values(current):
            if self._has_forward_component(child, depth + 1, seen):
                return True
        return False

    def render_forward_blocks(self, current: Any, depth: int, seen: set[int]) -> list[str]:
        if current is None:
            return []
        if depth > self.max_depth:
            return [f"[嵌套层数超过限制，已在 depth={depth} 截断]"]

        marker = id(current)
        if marker in seen:
            return []
        seen.add(marker)

        blocks: list[str] = []
        nodes = self.extract_forward_nodes(current)
        if nodes:
            rendered = self.render_nodes(nodes, depth, seen)
            if rendered:
                blocks.append(rendered)

        for child in self.iter_nested_values(current):
            blocks.extend(self.render_forward_blocks(child, depth + 1, seen))
        return blocks

    def render_nodes(self, nodes: Sequence[Any], depth: int, seen: set[int]) -> str:
        lines = [f"[合并转发 depth={depth}]"]
        count = 0
        for node in nodes:
            if count >= self.max_nodes:
                lines.append("...[节点过多，已截断]")
                break
            rendered = self.render_single_node(node, depth, seen)
            if rendered:
                lines.append(rendered)
                count += 1
        return "\n".join(lines)

    def render_single_node(self, node: Any, depth: int, seen: set[int]) -> str:
        sender = self.node_sender_name(node)
        text_parts = self.node_text_parts(node)
        child_blocks = self.render_nested_from_node(node, depth + 1, seen)

        body_parts: list[str] = []
        if text_parts:
            body_parts.append(" ".join(text_parts))
        if child_blocks:
            body_parts.append("\n".join(child_blocks))
        if not body_parts:
            body_parts.append("[空白节点或非文本节点]")
        body = "\n".join(body_parts)
        return f"{sender}: {body}" if sender else body

    def render_nested_from_node(self, node: Any, depth: int, seen: set[int]) -> list[str]:
        nested: list[str] = []
        for child in self.iter_nested_values(node):
            nested.extend(self.render_forward_blocks(child, depth, seen))
        return nested

    def extract_forward_nodes(self, current: Any) -> list[Any]:
        direct_nodes = self.coerce_nodes(current)
        if direct_nodes:
            return direct_nodes

        if isinstance(current, Comp.Reply):
            nodes = self.coerce_nodes(getattr(current, "chain", None))
            if nodes:
                return nodes

        for attr in FORWARD_NODE_ATTRS:
            nodes = self.coerce_nodes(getattr(current, attr, None))
            if nodes:
                return nodes

        if isinstance(current, dict):
            for key in FORWARD_NODE_ATTRS:
                nodes = self.coerce_nodes(current.get(key))
                if nodes:
                    return nodes
        return []

    def coerce_nodes(self, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, Comp.Nodes):
            return list(getattr(value, "nodes", []) or [])
        if isinstance(value, Comp.Node):
            return [value]
        if isinstance(value, str):
            return []
        if isinstance(value, Sequence):
            items = list(value)
            if items and any(self.looks_like_node(item) for item in items):
                return items
        return []

    def looks_like_node(self, value: Any) -> bool:
        if isinstance(value, Comp.Node):
            return True
        if isinstance(value, dict):
            has_sender = any(key in value for key in ("uin", "name", "nickname", "sender", "sender_name"))
            has_content = any(key in value for key in ("content", "message", "message_chain", "chain", "messages"))
            return has_sender and has_content
        sender_like = any(hasattr(value, attr) for attr in ("uin", "name", "nickname", "sender", "sender_name"))
        content_like = any(hasattr(value, attr) for attr in ("content", "message", "message_chain", "chain", "messages"))
        return sender_like and content_like

    def node_sender_name(self, node: Any) -> str:
        for attr in ("name", "nickname", "sender_name"):
            value = getattr(node, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()

        sender = getattr(node, "sender", None)
        if isinstance(sender, str) and sender.strip():
            return sender.strip()
        if isinstance(sender, dict):
            for key in ("name", "nickname", "card", "user_name"):
                value = sender.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        if isinstance(node, dict):
            for key in ("name", "nickname", "sender_name"):
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        uin = getattr(node, "uin", None)
        if uin is None and isinstance(node, dict):
            uin = node.get("uin")
        return str(uin) if uin else ""

    def node_text_parts(self, node: Any) -> list[str]:
        message_like = None
        for attr in ("content", "message", "message_chain", "chain", "messages"):
            value = getattr(node, attr, None)
            if value is not None:
                message_like = value
                break

        if message_like is None and isinstance(node, dict):
            for key in ("content", "message", "message_chain", "chain", "messages"):
                value = node.get(key)
                if value is not None:
                    message_like = value
                    break
        return self.collect_plain_texts(message_like)

    def collect_plain_texts(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, Comp.Plain):
            text = getattr(value, "text", "")
            return [text] if text else []
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
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            texts: list[str] = []
            for item in value:
                texts.extend(self.collect_plain_texts(item))
            return texts
        text = getattr(value, "text", None)
        if isinstance(text, str) and text.strip():
            return [text.strip()]
        return []

    def extract_message_chains(self, message_obj: Any) -> list[Any]:
        if message_obj is None:
            return []
        for attr in MESSAGE_CHAIN_ATTRS:
            value = getattr(message_obj, attr, None)
            if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
                return list(value)
        if isinstance(message_obj, dict):
            for key in MESSAGE_CHAIN_ATTRS:
                value = message_obj.get(key)
                if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
                    return list(value)
        return []

    def iter_nested_values(self, value: Any) -> list[Any]:
        nested: list[Any] = []
        if value is None:
            return nested

        for attr in NESTED_ATTRS:
            child = getattr(value, attr, None)
            if child is not None:
                nested.append(child)

        if isinstance(value, dict):
            for key in NESTED_ATTRS:
                child = value.get(key)
                if child is not None:
                    nested.append(child)
            nested.extend(v for k, v in value.items() if k not in NESTED_ATTRS and not isinstance(v, (str, int, float, bool)))
        elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            nested.extend(list(value))
        return nested

    def has_explicit_at_bot(self, event: Any) -> bool:
        chains = self.extract_message_chains(getattr(event, "message_obj", None))
        return bool(chains) and any(isinstance(component, Comp.At) for component in chains)

    def truncate_text(self, text: str) -> str:
        if len(text) > self.max_chars:
            return text[: self.max_chars - 12].rstrip() + "...[已截断]"
        return text
