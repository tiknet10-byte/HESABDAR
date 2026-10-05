"""Command line tools.

  python manage.py run [--host 0.0.0.0] [--port 8000] [--open]   start the server (local mode)
  python manage.py create-user <username> <role> [--password ...]  add a user (owner/admin/accountant/receptionist/staff/ai_agent)
  python manage.py demo                                             fill the database with realistic demo data
  python manage.py backup                                           create an encrypted backup now
  python manage.py restore <name>                                   restore a backup by name
  python manage.py mcp --user ai                                    run the MCP server for Claude Desktop / Claude Code
"""
from __future__ import annotations

import argparse
import getpass
import sys
import threading
import webbrowser


def main() -> None:
    ap = argparse.ArgumentParser(description="Hesabdar salon accounting")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--host", default="127.0.0.1")
    r.add_argument("--port", type=int, default=8000)
    r.add_argument("--open", action="store_true")
    r.add_argument("--reload", action="store_true")
    u = sub.add_parser("create-user")
    u.add_argument("username")
    u.add_argument("role")
    u.add_argument("--password")
    u.add_argument("--full-name", default="")
    sub.add_parser("demo")
    sub.add_parser("backup")
    rs = sub.add_parser("restore")
    rs.add_argument("name")
    m = sub.add_parser("mcp")
    m.add_argument("--user", default="ai")
    a = ap.parse_args()

    if a.cmd == "run":
        import socket

        import uvicorn
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", a.port)) == 0:
                sys.exit(f"Port {a.port} is already in use - another HESABDAR window is probably still running. "
                         "Close all HESABDAR windows (or run update.bat) and start again.")
        if a.open:
            threading.Timer(1.5, lambda: webbrowser.open(f"http://{a.host}:{a.port}")).start()
        uvicorn.run("app.main:app", host=a.host, port=a.port, reload=a.reload, proxy_headers=True)
        return
    if a.cmd == "mcp":
        from app.ai.mcp_server import serve
        serve(a.user)
        return

    from app.main import init_db
    init_db()
    from app.core.db import SessionLocal

    if a.cmd == "create-user":
        from app.core.security import ROLES, hash_password, validate_password_strength
        from app.models import User
        if a.role not in ROLES:
            sys.exit(f"role must be one of {ROLES}")
        pw = a.password or getpass.getpass("password: ")
        validate_password_strength(pw)
        with SessionLocal() as db:
            db.add(User(username=a.username.lower(), full_name=a.full_name, role=a.role, password_hash=hash_password(pw)))
            db.commit()
        print("user created")
    elif a.cmd == "demo":
        from app.seed import seed_demo
        with SessionLocal() as db:
            print(seed_demo(db))
            db.commit()
    elif a.cmd == "backup":
        from app.services.backup import create_backup
        print(create_backup("cli"))
    elif a.cmd == "restore":
        from app.services.backup import restore_backup
        print(restore_backup(a.name))


if __name__ == "__main__":
    main()
