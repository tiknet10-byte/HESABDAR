"""Thin wrapper around the Anthropic SDK (optional dependency).

The whole system works without AI. When the `anthropic` package is installed and
credentials are available (ANTHROPIC_API_KEY or an `ant auth login` profile),
AI features light up: assistant chat, vision OCR of receipts and sales books,
and smarter parsing.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any

from ..core.config import get_settings

log = logging.getLogger("hesabdar.ai")
_clients: dict[tuple[str, str], object] = {}

MASK = "••••"


def config() -> dict:
    """AI settings: from the Settings page (stored encrypted in the database), falling back to environment."""
    from ..core.db import SessionLocal
    from ..core.security import decrypt_secret
    from ..services import settings_store

    with SessionLocal() as db:
        key = decrypt_secret(settings_store.get(db, "ai.api_key"))
        model = settings_store.get(db, "ai.model") or get_settings().ai_model
        base_url = settings_store.get(db, "ai.base_url") or ""
    return {"api_key": key, "model": model, "base_url": base_url}


def _env_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_PROFILE") or os.path.exists(os.path.expanduser("~/.config/anthropic")))


def available() -> bool:
    if not get_settings().ai_enabled:
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(config()["api_key"]) or _env_credentials()


def client(cfg: dict | None = None):  # noqa: ANN201
    import anthropic

    cfg = cfg or config()
    k = (cfg["api_key"], cfg["base_url"])
    if k not in _clients:
        kwargs = {}
        if cfg["api_key"]:
            kwargs["api_key"] = cfg["api_key"]
        if cfg["base_url"]:
            kwargs["base_url"] = cfg["base_url"]
        _clients[k] = anthropic.Anthropic(**kwargs)
    return _clients[k]


def create(**kwargs: Any):  # noqa: ANN201
    """messages.create with sensible defaults and server-side refusal fallback enabled."""
    cfg = config()
    params = {"model": cfg["model"], "max_tokens": 16000, **kwargs}
    return client(cfg).beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)


def test_connection() -> dict:
    """Small request to verify the key, model and network path."""
    try:
        resp = create(max_tokens=64, output_config={"effort": "low"},
                      messages=[{"role": "user", "content": "Reply with the single word: OK"}])
        return {"ok": True, "model": resp.model, "reply": text_of(resp)[:50]}
    except Exception as exc:  # show the real reason (invalid key, network, model...)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}


def text_of(response) -> str:  # noqa: ANN001
    return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")


def extract_json(text: str) -> Any:
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    body = m.group(1) if m else text
    start = min([i for i in (body.find("{"), body.find("[")) if i >= 0], default=0)
    return json.loads(body[start:])


def vision_extract(image_bytes: bytes, media_type: str, instruction: str) -> Any:
    """Send an image (receipt screenshot, sales-book photo, PDF) to Claude and get JSON back."""
    data = base64.standard_b64encode(image_bytes).decode()
    block = ({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
             if media_type == "application/pdf"
             else {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
    resp = create(
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": [block, {"type": "text", "text": instruction + "\nOnly output JSON."}]}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("AI declined the request")
    return extract_json(text_of(resp))
