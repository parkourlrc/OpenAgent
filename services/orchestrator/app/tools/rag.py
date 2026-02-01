from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..config import settings
from ..db import exec_many, exec_sql, from_json, q_all, q_one, to_json
from ..llm import client as llm
from .base import ToolContext, ToolSpec, register
from .docs import docs_parse


SUPPORTED_EXTS = {".txt", ".md", ".pdf", ".docx"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    text = text.strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


def _embed(model: str, texts: List[str]) -> List[List[float]]:
    # batch embeddings; keep batch size moderate
    out: List[List[float]] = []
    bs = 32
    for i in range(0, len(texts), bs):
        out.extend(llm.embeddings(model=model, inputs=texts[i:i+bs]))
    return out


def kb_ingest(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    paths = args.get("paths")
    chunk_size = int(args.get("chunk_size", 1200))
    overlap = int(args.get("chunk_overlap", 200))
    emb_model = args.get("embeddings_model", settings.model_embeddings)

    workspace_id = args.get("workspace_id")
    if not workspace_id:
        raise ValueError("workspace_id is required for kb.ingest")

    ws_root = ctx.workspace_root.resolve()

    rel_paths: List[str] = []
    if paths:
        rel_paths = [str(p) for p in paths]
    else:
        for root, _, files in os.walk(ws_root):
            for fn in files:
                p = Path(root) / fn
                if p.suffix.lower() in SUPPORTED_EXTS:
                    rel_paths.append(str(p.relative_to(ws_root)))

    ingested = 0
    skipped = 0
    chunk_rows: List[Tuple[Any, ...]] = []
    doc_rows: List[Tuple[Any, ...]] = []

    for rel in rel_paths:
        p = (ws_root / rel).resolve()
        if not str(p).startswith(str(ws_root)):
            continue
        if not p.exists() or p.is_dir():
            continue
        if p.suffix.lower() not in SUPPORTED_EXTS:
            continue

        sha = _sha256(p)
        doc = q_one("SELECT * FROM kb_docs WHERE workspace_id=? AND sha256=?", (workspace_id, sha))
        if doc and doc.get("indexed_at"):
            skipped += 1
            continue

        # ensure doc record
        doc_id = doc["id"] if doc else hashlib.md5(f"{workspace_id}:{sha}".encode()).hexdigest()
        if not doc:
            doc_rows.append((doc_id, workspace_id, p.name, str(p), sha, None, _now()))
            exec_sql(
                "INSERT INTO kb_docs (id, workspace_id, filename, path, sha256, indexed_at, created_at) VALUES (?,?,?,?,?,?,?)",
                (doc_id, workspace_id, p.name, str(p), sha, None, _now()),
            )

        # parse
        parsed = docs_parse(ctx, {"path": rel, "max_chars": 800_000})
        text = parsed.get("text", "")
        chunks = _chunk_text(text, chunk_size=chunk_size, overlap=overlap)
        if not chunks:
            skipped += 1
            continue

        vecs = _embed(emb_model, chunks)
        # delete old chunks for this doc if any
        exec_sql("DELETE FROM kb_chunks WHERE doc_id=?", (doc_id,))
        for idx, (chunk, vec) in enumerate(zip(chunks, vecs)):
            arr = np.array(vec, dtype=np.float32)
            blob = arr.tobytes()
            chunk_id = hashlib.md5(f"{doc_id}:{idx}".encode()).hexdigest()
            chunk_rows.append((chunk_id, doc_id, idx, chunk, blob, arr.shape[0], _now()))
        exec_many(
            "INSERT INTO kb_chunks (id, doc_id, chunk_idx, text, embedding_blob, embedding_dim, created_at) VALUES (?,?,?,?,?,?,?)",
            chunk_rows,
        )
        chunk_rows.clear()
        exec_sql("UPDATE kb_docs SET indexed_at=? WHERE id=?", (_now(), doc_id))
        ingested += 1

    return {"ok": True, "ingested_docs": ingested, "skipped_docs": skipped, "embeddings_model": emb_model}


def kb_query(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    workspace_id = args.get("workspace_id")
    if not workspace_id:
        raise ValueError("workspace_id is required for kb.query")
    query = args["query"]
    top_k = int(args.get("top_k", 6))
    emb_model = args.get("embeddings_model", settings.model_embeddings)

    qvec = _embed(emb_model, [query])[0]
    qarr = np.array(qvec, dtype=np.float32)
    qnorm = np.linalg.norm(qarr) + 1e-12

    rows = q_all(
        """
        SELECT c.id AS chunk_id, c.text AS chunk_text, c.embedding_blob AS embedding_blob, c.embedding_dim AS embedding_dim,
               d.filename AS filename, d.path AS path, d.sha256 AS sha256
        FROM kb_chunks c
        JOIN kb_docs d ON c.doc_id = d.id
        WHERE d.workspace_id=?
        """,
        (workspace_id,),
    )

    scored = []
    for r in rows:
        emb_blob = r["embedding_blob"]
        dim = int(r["embedding_dim"])
        carr = np.frombuffer(emb_blob, dtype=np.float32, count=dim)
        denom = (np.linalg.norm(carr) + 1e-12) * qnorm
        sim = float(np.dot(carr, qarr) / denom)
        scored.append((sim, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for sim, r in scored[:top_k]:
        results.append(
            {
                "score": sim,
                "chunk_id": r["chunk_id"],
                "text": r["chunk_text"],
                "source": {"filename": r["filename"], "path": r["path"], "sha256": r["sha256"]},
            }
        )

    return {"ok": True, "results": results, "embeddings_model": emb_model}


def register_kb_tools() -> None:
    register(
        ToolSpec(
            name="kb.ingest",
            description="Ingest documents from a workspace into a simple vector index (SQLite) using OpenAI-compatible embeddings.",
            json_schema={
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string"},
                    "paths": {"type": "array", "items": {"type": "string"}},
                    "chunk_size": {"type": "integer", "default": 1200},
                    "chunk_overlap": {"type": "integer", "default": 200},
                    "embeddings_model": {"type": "string"},
                },
                "required": ["workspace_id"],
            },
            func=kb_ingest,
            risky=True,
        )
    )
    register(
        ToolSpec(
            name="kb.query",
            description="Query the workspace vector index with a natural language query; returns top-k chunks with sources.",
            json_schema={
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string"},
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 6},
                    "embeddings_model": {"type": "string"},
                },
                "required": ["workspace_id", "query"],
            },
            func=kb_query,
            risky=False,
        )
    )
