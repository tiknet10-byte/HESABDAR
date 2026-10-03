"""Discovers, loads, enables/disables plugins and wires them into the app."""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path

from fastapi import FastAPI

from ..core.config import get_settings
from ..core.db import SessionLocal
from ..core.events import bus
from ..models import PluginState
from .base import AITool, BasePlugin

log = logging.getLogger("hesabdar.plugins")


class PluginManager:
    def __init__(self) -> None:
        self.plugins: dict[str, BasePlugin] = {}
        self.enabled: dict[str, bool] = {}

    # discovery -------------------------------------------------------------
    def discover(self) -> None:
        builtin = Path(__file__).parent / "builtin"
        for pkg in sorted(p for p in builtin.iterdir() if (p / "plugin.py").exists()):
            self._load(f"app.plugins.builtin.{pkg.name}.plugin")
        ext = get_settings().external_plugins_dir
        if ext.exists():
            for pkg in sorted(p for p in ext.iterdir() if (p / "plugin.py").exists()):
                spec = importlib.util.spec_from_file_location(f"hesabdar_ext_{pkg.name}", pkg / "plugin.py")
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = module
                    try:
                        spec.loader.exec_module(module)
                        self._register(module)
                    except Exception:
                        log.exception("failed to load external plugin %s", pkg.name)

    def _load(self, module_name: str) -> None:
        try:
            self._register(importlib.import_module(module_name))
        except Exception:
            log.exception("failed to load plugin %s", module_name)

    def _register(self, module) -> None:  # noqa: ANN001
        plugin: BasePlugin = module.Plugin()
        plugin.setup()
        self.plugins[plugin.name] = plugin

    # state -------------------------------------------------------------------
    def sync_state(self) -> None:
        with SessionLocal() as db:
            for name, plugin in self.plugins.items():
                st = db.get(PluginState, name)
                if st is None:
                    st = PluginState(name=name, enabled=plugin.status != "concept", config=dict(plugin.default_config))
                    db.add(st)
                plugin.ctx.config = {**plugin.default_config, **(st.config or {})}
                self.enabled[name] = st.enabled
            db.commit()
        for name, plugin in self.plugins.items():
            for event, handler in plugin.events.items():
                bus.subscribe(event, self._guard(name, handler))

    def _guard(self, name: str, handler):  # noqa: ANN001, ANN202
        def wrapped(event: str, payload: dict) -> None:
            if self.enabled.get(name):
                handler(event, payload)
        return wrapped

    def set_enabled(self, name: str, enabled: bool) -> None:
        plugin = self.plugins[name]
        with SessionLocal() as db:
            st = db.get(PluginState, name) or PluginState(name=name)
            st.enabled = enabled
            db.merge(st)
            db.commit()
        self.enabled[name] = enabled
        (plugin.on_enable if enabled else plugin.on_disable)()

    def set_config(self, name: str, config: dict) -> dict:
        plugin = self.plugins[name]
        with SessionLocal() as db:
            st = db.get(PluginState, name) or PluginState(name=name, enabled=True)
            st.config = config
            db.merge(st)
            db.commit()
        plugin.ctx.config = {**plugin.default_config, **config}
        return plugin.ctx.config

    # wiring -------------------------------------------------------------------
    def mount(self, app: FastAPI) -> None:
        from ..api.deps import plugin_enabled_dependency

        for name, plugin in self.plugins.items():
            if plugin.router is not None:
                app.include_router(plugin.router, prefix=f"/api/plugins/{name}", tags=[f"plugin:{name}"],
                                   dependencies=[plugin_enabled_dependency(name)])

    def ai_tools(self) -> list[AITool]:
        tools: list[AITool] = []
        for name, plugin in self.plugins.items():
            if self.enabled.get(name):
                tools.extend(plugin.ai_tools())
        return tools

    def jobs(self):  # noqa: ANN201
        for name, plugin in self.plugins.items():
            for interval, fn in plugin.jobs():
                yield name, interval, fn

    def list(self) -> list[dict]:
        return [{**p.info(), "enabled": self.enabled.get(n, False), "config": p.ctx.config} for n, p in self.plugins.items()]


manager = PluginManager()
