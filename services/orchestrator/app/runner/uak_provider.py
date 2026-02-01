from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import httpx

from uak.agent.models import LLMResponse
from uak.agent.providers import OpenAIChatProvider


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.append(_coerce_text(item))
        return "".join([p for p in parts if p])
    if isinstance(value, dict):
        # Common patterns:
        # - {"content": "..."}
        # - {"text": "..."}
        # - {"text": {"value": "..."}}
        # - {"type":"text","text":{"value":"..."}}
        for key in ("text", "content", "value"):
            v = value.get(key)
            if v is None:
                continue
            if isinstance(v, str) and v:
                return v
            nested = _coerce_text(v)
            if nested:
                return nested
        return ""
    return ""


class WorkbenchOpenAIChatProvider(OpenAIChatProvider):
    """
    Workbench-specific OpenAI-compatible provider tweaks:
    - Some gateways (e.g. 0-0.pro) stream tokens via `delta.reasoning_content` while `delta.content` stays empty.
      We keep tool-calling behavior intact, and only fall back to reasoning_content as user-visible content when the
      stream finishes with no tool calls and no regular content.
    - Tolerate structured `content` blocks (list/dict) by extracting `text` where possible.
    - Support legacy streaming `delta.function_call` in addition to `delta.tool_calls`.
    """

    async def _chat_non_stream(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> LLMResponse:
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                body = ""
                try:
                    body = (resp.text or "")[:2000]
                except Exception:
                    body = ""
                try:
                    logging.getLogger("owb.llm").warning(
                        "llm_http_error status=%s url=%s body=%s",
                        getattr(resp, "status_code", None),
                        url,
                        body[:800],
                    )
                except Exception:
                    pass
                raise httpx.HTTPStatusError(f"{e} | body={body}", request=e.request, response=e.response) from None
            data = resp.json()

        choice = (data.get("choices") or [{}])[0] if isinstance(data, dict) else {}
        msg = choice.get("message") if isinstance(choice, dict) else {}
        msg = msg if isinstance(msg, dict) else {}

        content = _coerce_text(msg.get("content"))
        tool_calls = self._parse_tool_calls_from_message(msg)

        if not content and not tool_calls:
            content = _coerce_text(msg.get("reasoning_content")) or _coerce_text(msg.get("reasoning"))

        raw = data if isinstance(data, dict) else None
        return LLMResponse(content=content, tool_calls=tool_calls, raw=raw)

    async def _chat_stream(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        include_usage: bool,
    ) -> LLMResponse:
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        if include_usage:
            stream_payload["stream_options"] = {"include_usage": True}

        # Retry on "empty stream" quirks or transient timeouts from some gateways.
        max_attempts = 3
        stream_timeout = httpx.Timeout(
            timeout=self.timeout_s,
            connect=self.timeout_s,
            read=max(self.timeout_s, 300.0),
            write=self.timeout_s,
            pool=self.timeout_s,
        )
        transport = httpx.AsyncHTTPTransport(retries=2)

        for attempt in range(1, max_attempts + 1):
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            usage: Optional[dict[str, Any]] = None
            tool_calls_acc: dict[int, dict[str, Any]] = {}
            stream_error: Optional[str] = None
            response_ctype = ""
            response_status: int = 0

            stream_headers = dict(headers)
            stream_headers.setdefault("Accept", "text/event-stream")

            async with httpx.AsyncClient(timeout=stream_timeout, transport=transport) as client:
                async with client.stream("POST", url, json=stream_payload, headers=stream_headers) as resp:
                    try:
                        resp.raise_for_status()
                    except httpx.HTTPStatusError as e:
                        body = ""
                        try:
                            raw = await resp.aread()
                            body = raw.decode("utf-8", errors="replace")[:2000]
                        except Exception:
                            body = ""
                        try:
                            logging.getLogger("owb.llm").warning(
                                "llm_http_error status=%s url=%s body=%s",
                                getattr(resp, "status_code", None),
                                url,
                                body[:800],
                            )
                        except Exception:
                            pass
                        raise httpx.HTTPStatusError(f"{e} | body={body}", request=e.request, response=e.response) from None

                    response_status = int(getattr(resp, "status_code", 0) or 0)
                    ctype = str(resp.headers.get("content-type") or "").lower()
                    response_ctype = ctype
                    if "text/event-stream" not in ctype:
                        # Some gateways ignore stream=true and return a normal JSON body.
                        try:
                            raw = await resp.aread()
                            data = json.loads(raw.decode("utf-8", errors="replace"))
                        except Exception:
                            data = None
                        if isinstance(data, dict):
                            choice = (data.get("choices") or [{}])[0] if isinstance(data.get("choices"), list) else {}
                            msg = choice.get("message") if isinstance(choice, dict) else {}
                            msg = msg if isinstance(msg, dict) else {}
                            content = _coerce_text(msg.get("content"))
                            tool_calls = self._parse_tool_calls_from_message(msg)
                            if not content and not tool_calls:
                                content = _coerce_text(msg.get("reasoning_content")) or _coerce_text(msg.get("reasoning"))
                            return LLMResponse(content=content, tool_calls=tool_calls, raw=data)

                    data_buf: list[str] = []
                    raw_preview: list[str] = []

                    def _process_event_obj(event: dict[str, Any]) -> None:
                        nonlocal usage, stream_error
                        if stream_error:
                            return
                        err = event.get("error")
                        if isinstance(err, dict):
                            msg = str(err.get("message") or err.get("error") or err.get("type") or "unknown_error")
                            stream_error = msg[:800]
                            return
                        if isinstance(event.get("usage"), dict):
                            usage = event["usage"]
                        choices = event.get("choices")
                        if not isinstance(choices, list):
                            return
                        for choice in choices:
                            if not isinstance(choice, dict):
                                continue
                            delta = choice.get("delta")
                            if isinstance(delta, dict):
                                delta_content = _coerce_text(delta.get("content"))
                                if delta_content:
                                    content_parts.append(delta_content)
                                delta_reasoning = _coerce_text(delta.get("reasoning_content")) or _coerce_text(delta.get("reasoning"))
                                if delta_reasoning:
                                    reasoning_parts.append(delta_reasoning)
                                delta_tool_calls = delta.get("tool_calls")
                                if isinstance(delta_tool_calls, list):
                                    for tc in delta_tool_calls:
                                        if not isinstance(tc, dict):
                                            continue
                                        idx = tc.get("index")
                                        try:
                                            index = int(idx)
                                        except Exception:
                                            index = 0
                                        entry = tool_calls_acc.setdefault(
                                            index,
                                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                                        )
                                        tc_id = tc.get("id")
                                        if isinstance(tc_id, str) and tc_id:
                                            entry["id"] = tc_id
                                        fn = tc.get("function")
                                        if isinstance(fn, dict):
                                            name = fn.get("name")
                                            if isinstance(name, str) and name:
                                                entry["function"]["name"] = name
                                            args_part = fn.get("arguments")
                                            if isinstance(args_part, str) and args_part:
                                                entry["function"]["arguments"] = str(entry["function"].get("arguments") or "") + args_part

                                delta_fn_call = delta.get("function_call")
                                if isinstance(delta_fn_call, dict):
                                    entry = tool_calls_acc.setdefault(
                                        0,
                                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                                    )
                                    name = delta_fn_call.get("name")
                                    if isinstance(name, str) and name:
                                        entry["function"]["name"] = name
                                    args_part = delta_fn_call.get("arguments")
                                    if isinstance(args_part, str) and args_part:
                                        entry["function"]["arguments"] = str(entry["function"].get("arguments") or "") + args_part
                                continue

                            # Some gateways stream full "message" objects instead of "delta".
                            msg_obj = choice.get("message")
                            if isinstance(msg_obj, dict):
                                c = _coerce_text(msg_obj.get("content"))
                                if c:
                                    content_parts.append(c)
                                r = _coerce_text(msg_obj.get("reasoning_content")) or _coerce_text(msg_obj.get("reasoning"))
                                if r:
                                    reasoning_parts.append(r)
                                tcs = msg_obj.get("tool_calls")
                                if isinstance(tcs, list) and tcs:
                                    tool_calls_acc.setdefault(0, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})

                    async for raw_line in resp.aiter_lines():
                        if raw_line is None:
                            continue
                        line = str(raw_line).rstrip("\r")
                        if len(raw_preview) < 20 and line:
                            raw_preview.append(line[:500])

                        # End of SSE event: attempt to parse accumulated data.
                        if line == "":
                            if not data_buf:
                                continue
                            data_str = "".join(data_buf).strip()
                            data_buf = []
                            if not data_str:
                                continue
                            if data_str == "[DONE]":
                                break
                            try:
                                obj = json.loads(data_str)
                            except Exception:
                                continue
                            if isinstance(obj, dict):
                                _process_event_obj(obj)
                            continue

                        s = line.lstrip()
                        if not s.startswith("data:"):
                            continue
                        part = s[len("data:") :].lstrip()
                        if not part:
                            continue
                        if part == "[DONE]":
                            break
                        data_buf.append(part)

                        # Try parsing eagerly; helps when servers omit blank line delimiters.
                        candidate = "".join(data_buf).strip()
                        if candidate and candidate[0] in "{[":
                            try:
                                obj = json.loads(candidate)
                            except Exception:
                                obj = None
                            if isinstance(obj, dict):
                                data_buf = []
                                _process_event_obj(obj)

            if stream_error:
                raise RuntimeError(f"gateway_stream_error: {stream_error}")

            content = "".join(content_parts)
            reasoning = "".join(reasoning_parts)
            tool_calls_sorted = [tool_calls_acc[k] for k in sorted(tool_calls_acc.keys())]
            msg: dict[str, Any] = {"content": content, "tool_calls": tool_calls_sorted}
            if reasoning:
                msg["reasoning_content"] = reasoning
            tool_calls = self._parse_tool_calls_from_message(msg)

            # Best-effort: fall back to reasoning_content for display.
            if not content and not tool_calls and reasoning:
                content = reasoning
                msg["content"] = content

            raw: dict[str, Any] = {"choices": [{"message": msg}]}
            if usage is not None:
                raw["usage"] = usage
            raw["owb_stream_meta"] = {
                "attempt": attempt,
                "max_attempts": max_attempts,
                "status": response_status,
                "content_type": response_ctype,
            }

            # If still empty, retry once; otherwise return an empty response and let the engine decide.
            if not content and not tool_calls:
                if attempt < max_attempts:
                    continue
                raw["owb_stream_empty"] = True
                raw["owb_stream_preview"] = raw_preview
                return LLMResponse(content="", tool_calls=[], raw=raw)

            return LLMResponse(content=content, tool_calls=tool_calls, raw=raw)

        # Should be unreachable.
        return LLMResponse(content="", tool_calls=[], raw={"error": "stream_retry_exhausted"})
