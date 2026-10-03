"""Minimal MCP (Model Context Protocol) server over stdio.

Lets Claude Desktop, Claude Code or any MCP client talk to the salon's books
with exactly the same tools and permissions as the in-app assistant.

Usage (Claude Desktop / Claude Code config):
  {"mcpServers": {"hesabdar": {"command": "<path>/backend/.venv/bin/python",
                               "args": ["-m", "app.ai.mcp_server", "--user", "ai"],
                               "cwd": "<path>/backend"}}}
The --user must be an active user (create one with role "ai_agent" for least privilege).
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from ..core.db import SessionLocal
from ..models import User
from ..plugins.manager import manager
from .tools import invoke, schema, tools_for

PROTOCOL = "2025-06-18"


def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve(username: str) -> None:
    manager.discover()
    manager.sync_state()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username, User.is_active.is_(True)))
        if user is None:
            sys.stderr.write(f"user {username!r} not found or inactive\n")
            sys.exit(1)
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except ValueError:
            continue
        mid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        if mid is None:  # notification
            continue
        try:
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion", PROTOCOL),
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "hesabdar", "version": "1.0.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [{"name": t.name, "description": t.description, "inputSchema": schema(t)["input_schema"]}
                                    for t in tools_for(user)]}
            elif method == "tools/call":
                with SessionLocal() as db:
                    u = db.get(User, user.id)
                    try:
                        out = invoke(db, u, params["name"], params.get("arguments") or {})
                        result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]}
                    except Exception as exc:
                        db.rollback()
                        result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            else:
                _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}})
                continue
            _send({"jsonrpc": "2.0", "id": mid, "result": result})
        except Exception as exc:
            _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(exc)}})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="ai")
    serve(ap.parse_args().user)
