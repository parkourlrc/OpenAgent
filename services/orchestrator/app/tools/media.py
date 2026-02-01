from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import settings
from ..llm import client as llm
from .base import ToolContext, ToolSpec, register


def _artifact_dir(ctx: ToolContext) -> Path:
    p = settings.artifacts_dir / ctx.task_id / ctx.step_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def media_image_generate(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    prompt = args["prompt"]
    size = args.get("size", "1024x1024")
    n = int(args.get("n", 1))
    model = args.get("model", settings.model_image)
    out_prefix = args.get("filename_prefix", "image")
    data = llm.images_generate(model=model, prompt=prompt, size=size, n=n)
    out = []
    art_dir = _artifact_dir(ctx)
    for i, item in enumerate(data.get("data", [])):
        b64 = item.get("b64_json")
        if b64:
            img_bytes = base64.b64decode(b64)
            fname = f"{out_prefix}_{i+1}.png"
            (art_dir / fname).write_bytes(img_bytes)
            out.append({"file": str(art_dir / fname), "format": "png"})
        elif item.get("url"):
            out.append({"url": item["url"]})
    return {"ok": True, "results": out}


def media_image_edit(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    prompt = args["prompt"]
    image_path = Path(args["image_path"])
    mask_path = Path(args["mask_path"]) if args.get("mask_path") else None
    size = args.get("size", "1024x1024")
    n = int(args.get("n", 1))
    model = args.get("model", settings.model_image)
    out_prefix = args.get("filename_prefix", "edit")
    data = llm.images_edit(model=model, prompt=prompt, image_path=image_path, mask_path=mask_path, size=size, n=n)
    out = []
    art_dir = _artifact_dir(ctx)
    for i, item in enumerate(data.get("data", [])):
        b64 = item.get("b64_json")
        if b64:
            img_bytes = base64.b64decode(b64)
            fname = f"{out_prefix}_{i+1}.png"
            (art_dir / fname).write_bytes(img_bytes)
            out.append({"file": str(art_dir / fname), "format": "png"})
        elif item.get("url"):
            out.append({"url": item["url"]})
    return {"ok": True, "results": out}


def media_audio_transcribe(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    audio_path = Path(args["audio_path"])
    language = args.get("language")
    model = args.get("model", settings.model_audio_transcribe)
    data = llm.audio_transcribe(model=model, audio_path=audio_path, language=language)
    return {"ok": True, "transcription": data.get("text"), "raw": data}


def media_audio_speech(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    text = args["text"]
    voice = args.get("voice", "alloy")
    fmt = args.get("format", "mp3")
    model = args.get("model", settings.model_audio_speech)
    audio_bytes = llm.audio_speech(model=model, text=text, voice=voice, format=fmt)
    art_dir = _artifact_dir(ctx)
    fname = args.get("filename", f"speech.{fmt}")
    out_path = art_dir / fname
    out_path.write_bytes(audio_bytes)
    return {"ok": True, "file": str(out_path), "format": fmt}


def media_video_generate(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    prompt = args["prompt"]
    model = args.get("model", settings.model_video)
    size = args.get("size")
    duration_seconds = args.get("duration_seconds")
    data = llm.videos_generate(model=model, prompt=prompt, size=size, duration_seconds=duration_seconds)
    return {"ok": True, "raw": data}


def media_video_status(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    video_id = args["video_id"]
    data = llm.videos_status(video_id=video_id)
    return {"ok": True, "raw": data}


def media_video_retrieve(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    video_id = args["video_id"]
    fname = args.get("filename", f"{video_id}.mp4")
    video_bytes = llm.videos_retrieve(video_id=video_id)
    art_dir = _artifact_dir(ctx)
    out_path = art_dir / fname
    out_path.write_bytes(video_bytes)
    return {"ok": True, "file": str(out_path), "video_id": video_id}


def media_video_remix(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    model = args.get("model", settings.model_video)
    prompt = args["prompt"]
    video_id = args["video_id"]
    reference_image_path = Path(args["reference_image_path"]) if args.get("reference_image_path") else None
    data = llm.videos_remix(model=model, prompt=prompt, video_id=video_id, reference_image_path=reference_image_path)
    return {"ok": True, "raw": data}


def register_media_tools() -> None:
    register(
        ToolSpec(
            name="media.image_generate",
            description="Generate image(s) via OpenAI-compatible /images/generations and save as artifacts.",
            json_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "size": {"type": "string", "default": "1024x1024"},
                    "n": {"type": "integer", "default": 1},
                    "model": {"type": "string", "description": "OpenAI-compatible image model id"},
                    "filename_prefix": {"type": "string", "default": "image"},
                },
                "required": ["prompt"],
            },
            func=media_image_generate,
            risky=False,
        )
    )
    register(
        ToolSpec(
            name="media.image_edit",
            description="Edit an image (optionally with a mask) via OpenAI-compatible /images/edits and save as artifacts.",
            json_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "image_path": {"type": "string", "description": "path to input image on server filesystem"},
                    "mask_path": {"type": "string", "description": "optional path to mask image on server filesystem"},
                    "size": {"type": "string", "default": "1024x1024"},
                    "n": {"type": "integer", "default": 1},
                    "model": {"type": "string"},
                    "filename_prefix": {"type": "string", "default": "edit"},
                },
                "required": ["prompt", "image_path"],
            },
            func=media_image_edit,
            risky=True,
        )
    )
    register(
        ToolSpec(
            name="media.audio_transcribe",
            description="Transcribe speech to text via OpenAI-compatible /audio/transcriptions.",
            json_schema={
                "type": "object",
                "properties": {
                    "audio_path": {"type": "string"},
                    "language": {"type": "string"},
                    "model": {"type": "string"},
                },
                "required": ["audio_path"],
            },
            func=media_audio_transcribe,
            risky=False,
        )
    )
    register(
        ToolSpec(
            name="media.audio_speech",
            description="Text-to-speech via OpenAI-compatible /audio/speech, saved as artifact.",
            json_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "voice": {"type": "string", "default": "alloy"},
                    "format": {"type": "string", "default": "mp3"},
                    "filename": {"type": "string"},
                    "model": {"type": "string"},
                },
                "required": ["text"],
            },
            func=media_audio_speech,
            risky=False,
        )
    )
    register(
        ToolSpec(
            name="media.video_generate",
            description="Generate a video via OpenAI-compatible /videos/generations. Returns provider job payload.",
            json_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "size": {"type": "string"},
                    "duration_seconds": {"type": "integer"},
                    "model": {"type": "string"},
                },
                "required": ["prompt"],
            },
            func=media_video_generate,
            risky=False,
        )
    )
    register(
        ToolSpec(
            name="media.video_status",
            description="Check video generation status via OpenAI-compatible /videos/status/{video_id}.",
            json_schema={
                "type": "object",
                "properties": {"video_id": {"type": "string"}},
                "required": ["video_id"],
            },
            func=media_video_status,
            risky=False,
        )
    )
    register(
        ToolSpec(
            name="media.video_retrieve",
            description="Retrieve a completed video via OpenAI-compatible /videos/retrieval/{video_id} and save as artifact.",
            json_schema={
                "type": "object",
                "properties": {
                    "video_id": {"type": "string"},
                    "filename": {"type": "string", "default": "video.mp4"},
                },
                "required": ["video_id"],
            },
            func=media_video_retrieve,
            risky=False,
        )
    )
    register(
        ToolSpec(
            name="media.video_remix",
            description="Remix/edit a video via OpenAI-compatible /videos/remix. Returns provider job payload.",
            json_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "video_id": {"type": "string"},
                    "reference_image_path": {"type": "string"},
                    "model": {"type": "string"},
                },
                "required": ["prompt", "video_id"],
            },
            func=media_video_remix,
            risky=True,
        )
    )
