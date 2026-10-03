"""Plugin (افزونه) contract.

A plugin is a Python package containing `plugin.py` that defines `class Plugin(BasePlugin)`.
Built-in plugins live in app/plugins/builtin/, third-party ones in backend/plugins/.

A plugin can:
  * add REST endpoints        -> `router` (mounted at /api/plugins/<name>/...)
  * react to business events  -> `events` mapping  {"deposit.created": handler}
  * give the AI new abilities -> `ai_tools()` returning AITool objects
  * run periodic jobs         -> `jobs()` returning [(interval_seconds, callable)]
  * add pages to the UI       -> `ui` manifest (menu entries rendered by the frontend)
  * expose settings           -> `config_schema` (JSON schema rendered as a form in the UI)
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter


@dataclass
class AITool:
    """A capability exposed to AI models (Claude tool use, MCP, or any function-calling LLM)."""
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Any]  # handler(db, user, **kwargs) -> JSON-serializable
    permission: str = "read"  # read | write | finance | reports | settings


@dataclass
class PluginContext:
    name: str
    config: dict = field(default_factory=dict)


class BasePlugin:
    name: str = "base"
    title: str = ""
    description: str = ""
    version: str = "0.1.0"
    author: str = ""
    category: str = "general"  # integration | ai | reports | operations
    status: str = "stable"  # stable | beta | concept
    config_schema: dict = {"type": "object", "properties": {}}
    default_config: dict = {}
    ui: list[dict] = []  # [{"path": "/plugins/x", "title": "...", "icon": "..."}]

    def __init__(self) -> None:
        self.router: APIRouter | None = None
        self.events: dict[str, Callable[[str, dict], None]] = {}
        self.ctx = PluginContext(self.name, dict(self.default_config))

    # lifecycle -----------------------------------------------------------
    def setup(self) -> None:
        """Build router/events. Called once when the plugin is loaded."""

    def on_enable(self) -> None: ...

    def on_disable(self) -> None: ...

    def ai_tools(self) -> list[AITool]:
        return []

    def jobs(self) -> list[tuple[int, Callable[[], None]]]:
        return []

    def info(self) -> dict:
        return {"name": self.name, "title": self.title, "description": self.description, "version": self.version,
                "author": self.author, "category": self.category, "status": self.status,
                "config_schema": self.config_schema, "ui": self.ui}
