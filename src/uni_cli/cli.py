"""uni: a read-only command line for your Canvas courses. Every command takes --json."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any

from . import commands
from .canvas import CanvasError, ReadOnlyError
from .config import ConfigError, load, setup


def _when(value: Any) -> str:
    if not isinstance(value, str):
        return "no date"
    return datetime.fromisoformat(value).astimezone().strftime("%a %b %d %I:%M %p")


def _human(command: str, data: dict) -> str:
    lines: list[str] = []
    if command == "courses":
        for x in data["courses"]:
            lines.append(f"{x['code']}  {x['name']}  ({x['term']})  {x['url']}")
            lines += [f"    {t['label']}: {t['url']}" for t in x["tools"]]
    elif command == "due":
        for i in data["items"]:
            lines.append(f"{_when(i['due'])}  {i['course']}  {i['title']}  [{i['status'] or i['kind']}]  {i['url'] or ''}")
        lines += [f"MISSING  {_when(m['due'])}  {m['title']}  {m['url'] or ''}" for m in data["missing"]]
        lines = lines or ["Nothing due."]
    elif command == "grades":
        for x in data["courses"]:
            score = f"{x['currentScore']}%" if x["currentScore"] is not None else "no score yet"
            lines.append(f"{x['code']}  {x['name']}  current {score}{' (' + x['currentGrade'] + ')' if x['currentGrade'] else ''}")
            lines += [f"    {m['title']}: {m['score']}/{m['outOf']}" + ("  (excused)" if m["excused"] else "") for m in x["marks"]]
    elif command == "announcements":
        for p in data["items"]:
            lines.append(f"{_when(p['at'])}  {p['course']}  {p['kind']}: {p['title']}" + (f" ({p['by']})" if p["by"] else ""))
            lines += [f"    {p['text']}"] if p["text"] else []
            lines.append(f"    {p['url']}  [{p['id']}]")
        lines = lines or ["Nothing new."]
    elif command == "changes":
        for i in data["items"]:
            lines.append(f"{_when(i['time'])}  {i['course']}  {i['kind']}: {i['title']}: {i['summary']}  {i['url'] or ''}")
        lines.append(f"cursor: {data['cursor']}")
    else:
        lines = [f"{k}: {v}" for k, v in data.items() if v not in (None, "", [])]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print JSON")
    parser = argparse.ArgumentParser(prog="uni", description="Read-only command line for your Canvas courses.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="save your Canvas address and access token")
    sub.add_parser("status", parents=[common], help="check the connection")
    p = sub.add_parser("courses", parents=[common], help="your courses, with ids and tool links")
    p.add_argument("--all", action="store_true", help="every term, not just current courses")
    p = sub.add_parser("due", parents=[common], help="what is due soon, and missing work")
    p.add_argument("--days", type=int, default=7)
    p = sub.add_parser("grades", parents=[common], help="current scores and posted marks")
    p.add_argument("course", nargs="?", help="course code or id")
    p = sub.add_parser("announcements", parents=[common], help="announcements and new files and pages")
    p.add_argument("course", nargs="?")
    p.add_argument("--since", default="7d", help="90m, 24h, 7d or an ISO date/instant (default 7d)")
    p = sub.add_parser("changes", parents=[common], help="everything new or changed since a time or cursor")
    p.add_argument("--since", default="24h")
    p.add_argument("--course")
    p = sub.add_parser("show", parents=[common], help="one item in full")
    p.add_argument("ref", help="assignment:<course id>:<id>, announcement:<course id>:<id>, page:<course id>:<url> or file:<id>")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "setup":
            print(setup())
            return 0
        canvas = load()
        if args.command == "courses":
            data = commands.courses(canvas, all_terms=args.all)
        elif args.command == "due":
            data = commands.due(canvas, args.days)
        elif args.command == "grades":
            data = commands.grades(canvas, args.course)
        elif args.command == "announcements":
            data = commands.announcements(canvas, args.course, args.since)
        elif args.command == "changes":
            data = commands.changes(canvas, args.since, args.course)
        elif args.command == "show":
            data = commands.show(canvas, args.ref)
        else:
            data = commands.status(canvas)
    except (ConfigError, commands.InputError, CanvasError, ReadOnlyError) as error:
        print(f"uni: {error}", file=sys.stderr)
        return 2 if isinstance(error, (ConfigError, commands.InputError)) else 1
    print(json.dumps(data, indent=2, ensure_ascii=False) if args.json else _human(args.command, data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
