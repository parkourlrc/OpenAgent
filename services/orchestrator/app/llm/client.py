from __future__ import annotations

import base64
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import requests

from ..config import settings


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    raw: Dict[str, Any]
    content: str
    tool_calls: List[Dict[str, Any]]


def _headers(api_key: Optional[str] = None) -> Dict[str, str]:
    key = api_key or os.getenv("OPENAI_API_KEY") or settings.llm_api_key
    # OpenAI-compatible expects Authorization Bearer
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _url(path: str) -> str:
    base = (os.getenv("OPENAI_BASE_URL") or settings.llm_base_url).rstrip("/")
    path = path.lstrip("/")
    return f"{base}/{path}"


def _is_gpt_family_model(model: str) -> bool:
    m = (model or "").lower()
    return "gpt" in m


def _iter_sse_data_lines(resp: requests.Response) -> Iterable[str]:
    """
    Iterate Server-Sent Events response lines, yielding decoded `data:` payloads.

    OpenAI-compatible streaming uses:
      data: {...json...}
      data: [DONE]
    """
    for raw in resp.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = str(raw).strip()
        if not line:
            continue
        if not line.startswith("data:"):
            continue
        yield line[len("data:") :].strip()


def _chat_streaming(
    *,
    model: str,
    payload: Dict[str, Any],
    timeout_s: float,
) -> LLMResponse:
    payload = dict(payload)
    payload["stream"] = True

    r = requests.post(
        _url("/chat/completions"),
        headers=_headers(),
        json=payload,
        timeout=timeout_s,
        stream=True,
    )
    if r.status_code >= 400:
        raise LLMError(f"chat/completions failed: {r.status_code} {r.text[:800]}")

    content_parts: List[str] = []
    tool_calls_by_index: Dict[int, Dict[str, Any]] = {}
    last_chunk: Dict[str, Any] = {}

    for data_line in _iter_sse_data_lines(r):
        if data_line == "[DONE]":
            break
        try:
            chunk = json.loads(data_line)
        except Exception:
            continue
        if not isinstance(chunk, dict):
            continue
        last_chunk = chunk
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = (choices[0] or {}).get("delta") or {}
        if isinstance(delta, dict):
            if delta.get("content"):
                content_parts.append(str(delta.get("content")))
            if delta.get("tool_calls"):
                for tc in delta.get("tool_calls") or []:
                    try:
                        idx = int(tc.get("index") if tc.get("index") is not None else 0)
                    except Exception:
                        idx = 0
                    cur = tool_calls_by_index.get(idx)
                    if cur is None:
                        cur = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                        tool_calls_by_index[idx] = cur
                    if tc.get("id"):
                        cur["id"] = tc.get("id")
                    if tc.get("type"):
                        cur["type"] = tc.get("type")
                    fn = tc.get("function") or {}
                    if isinstance(fn, dict):
                        if fn.get("name"):
                            cur["function"]["name"] = fn.get("name")
                        if fn.get("arguments"):
                            cur["function"]["arguments"] += str(fn.get("arguments"))

    tool_calls = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index.keys())]
    content = "".join(content_parts)

    raw = last_chunk or {"model": model, "choices": []}
    # Normalize into a non-streaming-like shape for downstream code.
    raw = dict(raw)
    raw.setdefault("model", model)
    raw["choices"] = [
        {
            "index": 0,
            "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
        }
    ]
    return LLMResponse(raw=raw, content=content, tool_calls=tool_calls)


def chat(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    response_format: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
    timeout_s: float = 120,
) -> LLMResponse:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if response_format is not None:
        payload["response_format"] = response_format
    if extra:
        payload.update(extra)

    must_stream = _is_gpt_family_model(model)
    if must_stream:
        return _chat_streaming(model=model, payload=payload, timeout_s=timeout_s)

    r = requests.post(_url("/chat/completions"), headers=_headers(), json=payload, timeout=timeout_s)
    if r.status_code >= 400:
        raise LLMError(f"chat/completions failed: {r.status_code} {r.text[:800]}")
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    tool_calls = msg.get("tool_calls") or []
    return LLMResponse(raw=data, content=content, tool_calls=tool_calls)


def embeddings(*, model: str, inputs: List[str]) -> List[List[float]]:
    payload = {"model": model, "input": inputs}
    r = requests.post(_url("/embeddings"), headers=_headers(), json=payload, timeout=120)
    if r.status_code >= 400:
        raise LLMError(f"embeddings failed: {r.status_code} {r.text[:800]}")
    data = r.json()
    out = []
    for item in data.get("data", []):
        out.append(item["embedding"])
    if len(out) != len(inputs):
        raise LLMError(f"embeddings length mismatch: got {len(out)} expected {len(inputs)}")
    return out


def images_generate(*, model: str, prompt: str, size: str = "1024x1024", n: int = 1) -> Dict[str, Any]:
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": n,
        # request b64 to avoid relying on external URL storage
        "response_format": "b64_json",
    }
    r = requests.post(_url("/images/generations"), headers=_headers(), json=payload, timeout=600)
    if r.status_code >= 400:
        raise LLMError(f"images/generations failed: {r.status_code} {r.text[:800]}")
    return r.json()


def images_edit(
    *,
    model: str,
    prompt: str,
    image_path: Path,
    mask_path: Optional[Path] = None,
    size: str = "1024x1024",
    n: int = 1,
) -> Dict[str, Any]:
    # OpenAI-compatible image edits use multipart/form-data
    url = _url("/images/edits")
    headers = {"Authorization": _headers()["Authorization"]}
    files = {
        "image": (image_path.name, image_path.read_bytes(), mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"),
    }
    if mask_path is not None:
        files["mask"] = (mask_path.name, mask_path.read_bytes(), mimetypes.guess_type(str(mask_path))[0] or "application/octet-stream")
    data = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": str(n),
        "response_format": "b64_json",
    }
    r = requests.post(url, headers=headers, files=files, data=data, timeout=600)
    if r.status_code >= 400:
        raise LLMError(f"images/edits failed: {r.status_code} {r.text[:800]}")
    return r.json()


def audio_transcribe(*, model: str, audio_path: Path, language: Optional[str] = None) -> Dict[str, Any]:
    url = _url("/audio/transcriptions")
    headers = {"Authorization": _headers()["Authorization"]}
    files = {
        "file": (audio_path.name, audio_path.read_bytes(), mimetypes.guess_type(str(audio_path))[0] or "application/octet-stream"),
    }
    data = {"model": model}
    if language:
        data["language"] = language
    r = requests.post(url, headers=headers, files=files, data=data, timeout=600)
    if r.status_code >= 400:
        raise LLMError(f"audio/transcriptions failed: {r.status_code} {r.text[:800]}")
    return r.json()


def audio_speech(*, model: str, text: str, voice: str = "alloy", format: str = "mp3") -> bytes:
    # OpenAI-compatible TTS endpoint
    payload = {"model": model, "input": text, "voice": voice, "format": format}
    r = requests.post(_url("/audio/speech"), headers=_headers(), json=payload, timeout=600)
    if r.status_code >= 400:
        raise LLMError(f"audio/speech failed: {r.status_code} {r.text[:800]}")
    return r.content


def videos_generate(*, model: str, prompt: str, size: Optional[str] = None, duration_seconds: Optional[int] = None, seconds: Optional[int] = None) -> Dict[str, Any]:
    """
    OpenAI-compatible video generation.
    Uses POST /videos (as in OpenAI Video API and LiteLLM proxy examples).
    """
    payload: Dict[str, Any] = {"model": model, "prompt": prompt}
    if size:
        payload["size"] = size
    # Providers differ: some use "seconds"; keep both if provided.
    if seconds is not None:
        payload["seconds"] = seconds
    if duration_seconds is not None:
        payload["duration_seconds"] = duration_seconds
        payload.setdefault("seconds", duration_seconds)
    r = requests.post(_url("/videos"), headers=_headers(), json=payload, timeout=600)
    if r.status_code >= 400:
        raise LLMError(f"videos failed: {r.status_code} {r.text[:800]}")
    return r.json()


def videos_status(*, video_id: str) -> Dict[str, Any]:
    """OpenAI-compatible video status: GET /videos/{video_id}."""
    r = requests.get(_url(f"/videos/{video_id}"), headers=_headers(), timeout=120)
    if r.status_code >= 400:
        raise LLMError(f"videos status failed: {r.status_code} {r.text[:800]}")
    return r.json()


def videos_retrieve(*, video_id: str) -> bytes:
    """OpenAI-compatible video content download: GET /videos/{video_id}/content."""
    r = requests.get(_url(f"/videos/{video_id}/content"), headers=_headers(), timeout=600)
    if r.status_code >= 400:
        raise LLMError(f"videos content failed: {r.status_code} {r.text[:800]}")
    return r.content


def videos_remix(*, model: str, prompt: str, video_id: str, reference_image_path: Optional[Path] = None) -> Dict[str, Any]:
    """OpenAI-compatible video remix: POST /videos/{video_id}/remix."""
    payload: Dict[str, Any] = {"model": model, "prompt": prompt}
    # Some providers may support a reference image; we send as base64 if provided
    if reference_image_path:
        b64 = base64.b64encode(reference_image_path.read_bytes()).decode("utf-8")
        payload["reference_image_b64"] = b64
        payload["reference_image_filename"] = reference_image_path.name
    r = requests.post(_url(f"/videos/{video_id}/remix"), headers=_headers(), json=payload, timeout=600)
    if r.status_code >= 400:
        raise LLMError(f"videos remix failed: {r.status_code} {r.text[:800]}")
    return r.json()
