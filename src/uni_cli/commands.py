"""What each command reads from Canvas and returns, as plain data (the --json output)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any

from .canvas import Canvas, CanvasError, parse_time


class InputError(Exception):
    """Something wrong with what was asked, rather than with Canvas."""


class _Text(HTMLParser):
    BLOCKS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_text(html: Any) -> str:
    if not isinstance(html, str) or not html:
        return ""
    parser = _Text()
    parser.feed(html)
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


def gist(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def now() -> datetime:
    return datetime.now().astimezone()


def iso(value: datetime | None) -> str | None:
    return value.astimezone().isoformat(timespec="seconds") if value else None


def parse_since(raw: str, at: datetime | None = None) -> datetime:
    """'90m', '24h', '7d', an ISO date (local midnight) or an ISO instant."""
    at = at or now()
    match = re.fullmatch(r"\s*(\d+)\s*([mhd])\s*", raw)
    if match:
        return at - timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[match.group(2)]: int(match.group(1))})
    try:
        value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        raise InputError(f"{raw!r} is not a time: use an ISO date or instant, or 90m, 24h, 7d") from None
    return value if value.tzinfo else value.astimezone()


# ---- courses ----

def _courses(c: Canvas, all_terms: bool = False) -> list[dict]:
    params: dict[str, Any] = {"include[]": ["term", "total_scores"]}
    if not all_terms:
        params["enrollment_state"] = "active"
    return [x for x in c.get_all("/api/v1/courses", params) if isinstance(x, dict) and "id" in x and not x.get("access_restricted_by_date")]


def find_course(courses: list[dict], query: str) -> dict:
    q = query.strip().lower()
    for course in courses:
        if str(course["id"]) == q:
            return course
    hits = [x for x in courses if str(x.get("course_code", "")).lower().startswith(q)]
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(str(x.get("course_code")) for x in courses)
    raise InputError(f"{query!r} {'matches several courses' if hits else 'is none of your courses'}: {names}")


def _enrollment(course: dict) -> dict:
    return next((e for e in course.get("enrollments") or [] if e.get("type") == "student"), {})


def courses(c: Canvas, all_terms: bool = False, tools: bool = True) -> dict:
    out = []
    for x in _courses(c, all_terms):
        links = []
        if tools:
            try:
                tabs = c.get_all(f"/api/v1/courses/{x['id']}/tabs")
            except CanvasError:
                tabs = []
            links = [{"label": t.get("label"), "url": t.get("html_url") and c.base + t["html_url"]} for t in tabs if t.get("type") == "external"]
        out.append({"id": x["id"], "code": x.get("course_code"), "name": x.get("name"), "term": (x.get("term") or {}).get("name"),
                    "url": f"{c.base}/courses/{x['id']}", "tools": links})
    return {"courses": out}


# ---- due ----

def _status(sub: Any) -> str | None:
    """None for something with nothing to hand in (an event)."""
    if not isinstance(sub, dict) or not sub:
        return None
    for key in ("graded", "missing", "late", "submitted", "excused"):
        if sub.get(key):
            return key
    return "not submitted"


def due(c: Canvas, days: int = 7) -> dict:
    if not 1 <= days <= 120:
        raise InputError("--days must be from 1 to 120")
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    items = []
    for p in c.get_all("/api/v1/planner/items", {"start_date": start.isoformat(), "end_date": end.isoformat()}):
        thing = p.get("plannable") or {}
        at = parse_time(thing.get("due_at") or thing.get("todo_date") or thing.get("start_at") or p.get("plannable_date"))
        url = p.get("html_url")
        items.append({"course": p.get("context_name"), "title": thing.get("title") or thing.get("name"), "kind": p.get("plannable_type"),
                      "due": iso(at), "status": _status(p.get("submissions")), "points": thing.get("points_possible"),
                      "url": c.base + url if isinstance(url, str) and url.startswith("/") else url, "source": "Canvas"})
    missing = [{"course": m.get("course_id"), "title": m.get("name"), "due": iso(parse_time(m.get("due_at"))), "url": m.get("html_url")}
               for m in c.get_all("/api/v1/users/self/missing_submissions", {"filter[]": "submittable"})]
    items.sort(key=lambda i: i["due"] or "")
    return {"from": iso(start), "to": iso(end), "items": items, "missing": missing}


# ---- grades ----

def _marks(c: Canvas, course_id: Any) -> list[dict]:
    out = []
    for a in c.get_all(f"/api/v1/courses/{course_id}/assignments", {"include[]": "submission", "order_by": "due_at"}):
        sub = a.get("submission") or {}
        if sub.get("score") is None and not sub.get("excused"):
            continue
        out.append({"title": a.get("name"), "score": sub.get("score"), "outOf": a.get("points_possible"), "grade": sub.get("grade"),
                    "excused": bool(sub.get("excused")), "gradedAt": iso(parse_time(sub.get("graded_at"))), "url": a.get("html_url")})
    return out


def grades(c: Canvas, course: str | None = None) -> dict:
    listed = _courses(c)
    chosen = [find_course(listed, course)] if course else listed
    out = []
    for x in chosen:
        e = _enrollment(x)
        out.append({"code": x.get("course_code"), "name": x.get("name"), "currentScore": e.get("computed_current_score"),
                    "currentGrade": e.get("computed_current_grade"), "finalScore": e.get("computed_final_score"), "marks": _marks(c, x["id"])})
    return {"courses": out}


# ---- announcements, files, pages ----

def _course_list(c: Canvas, course: str | None) -> list[dict]:
    listed = _courses(c)
    return [find_course(listed, course)] if course else listed


def _optional(c: Canvas, path: str, params: dict[str, Any]) -> list[Any]:
    """A list a course may hide from students (Files, Pages): hidden reads as empty."""
    try:
        return c.get_all(path, params)
    except CanvasError as error:
        if error.status in (401, 403, 404):
            return []
        raise


def _posts(c: Canvas, chosen: list[dict], since: datetime) -> list[dict]:
    out: list[dict] = []
    codes = {f"course_{x['id']}": x.get("course_code") for x in chosen}
    if codes:
        params = {"context_codes[]": list(codes), "start_date": since.isoformat(), "end_date": (now() + timedelta(days=1)).isoformat()}
        for a in c.get_all("/api/v1/announcements", params):
            out.append({"id": f"announcement:{str(a.get('context_code', '')).removeprefix('course_')}:{a.get('id')}", "kind": "announcement",
                        "course": codes.get(a.get("context_code")), "title": a.get("title"),
                        "by": (a.get("author") or {}).get("display_name"), "at": iso(parse_time(a.get("posted_at"))),
                        "url": a.get("html_url"), "text": gist(html_text(a.get("message")))})
    for x in chosen:
        for f in _optional(c, f"/api/v1/courses/{x['id']}/files", {"sort": "created_at", "order": "desc"}):
            at = parse_time(f.get("created_at"))
            if at and at >= since:
                out.append({"id": f"file:{f.get('id')}", "kind": "file", "course": x.get("course_code"), "title": f.get("display_name"),
                            "by": None, "at": iso(at), "url": f"{c.base}/courses/{x['id']}/files/{f.get('id')}", "text": None})
        for p in _optional(c, f"/api/v1/courses/{x['id']}/pages", {"sort": "created_at", "order": "desc"}):
            at = parse_time(p.get("created_at"))
            if at and at >= since:
                out.append({"id": f"page:{x['id']}:{p.get('url')}", "kind": "page", "course": x.get("course_code"), "title": p.get("title"),
                            "by": None, "at": iso(at), "url": p.get("html_url"), "text": None})
    out.sort(key=lambda i: i["at"] or "", reverse=True)
    return out


def announcements(c: Canvas, course: str | None = None, since: str = "7d") -> dict:
    after = parse_since(since)
    return {"since": iso(after), "items": _posts(c, _course_list(c, course), after)}


# ---- changes ----

def changes(c: Canvas, since: str, course: str | None = None) -> dict:
    """Everything new or changed after `since`, oldest first. Pass `cursor` back as --since next time."""
    asked_at = now()
    after = parse_since(since, asked_at)
    chosen = _course_list(c, course)
    items: list[dict] = []

    def add(at: datetime | None, kind: str, course_code: Any, title: Any, summary: str, url: Any) -> None:
        if at and at > after:
            items.append({"time": iso(at), "kind": kind, "course": course_code, "title": title, "summary": summary, "url": url})

    for x in chosen:
        code = x.get("course_code")
        for a in c.get_all(f"/api/v1/courses/{x['id']}/assignments", {"include[]": "submission"}):
            created, updated = parse_time(a.get("created_at")), parse_time(a.get("updated_at"))
            due_at = iso(parse_time(a.get("due_at")))
            if created and created > after:
                add(created, "assignment", code, a.get("name"), f"New assignment, due {due_at or 'no date'}", a.get("html_url"))
            elif updated:
                add(updated, "deadline" if a.get("due_at") else "assignment", code, a.get("name"), f"Assignment updated, due {due_at or 'no date'}", a.get("html_url"))
            sub = a.get("submission") or {}
            if sub.get("score") is not None:
                add(parse_time(sub.get("posted_at") or sub.get("graded_at")), "grade", code, a.get("name"),
                    f"Graded: {sub.get('score')}/{a.get('points_possible')}", a.get("html_url"))
    for p in _posts(c, chosen, after):
        add(parse_time(p["at"]), p["kind"], p["course"], p["title"], f"New {p['kind']}", p["url"])
    items.sort(key=lambda i: i["time"])
    return {"since": iso(after), "cursor": iso(asked_at), "items": items}


# ---- show ----

def show(c: Canvas, ref: str) -> dict:
    """assignment:<course id>:<id>, announcement:<course id>:<id>, page:<course id>:<page url> or file:<id>."""
    kind, _, rest = ref.partition(":")
    course_id, _, item = rest.partition(":")
    if kind == "file" and rest.isdigit():
        f = c.get(f"/api/v1/files/{rest}")
        return {"kind": "file", "title": f.get("display_name"), "size": f.get("size"), "contentType": f.get("content-type"),
                "created": f.get("created_at"), "updated": f.get("updated_at"), "url": f.get("url") and f"{c.base}/files/{rest}"}
    if not course_id.isdigit() or not item:
        raise InputError("show takes assignment:<course id>:<id>, announcement:<course id>:<id>, page:<course id>:<page url> or file:<id>")
    if kind == "assignment":
        a = c.get(f"/api/v1/courses/{course_id}/assignments/{item}", {"include[]": "submission"})
        sub = a.get("submission") or {}
        return {"kind": "assignment", "title": a.get("name"), "due": a.get("due_at"), "unlock": a.get("unlock_at"), "lock": a.get("lock_at"),
                "points": a.get("points_possible"), "status": _status(sub), "score": sub.get("score"), "url": a.get("html_url"),
                "description": html_text(a.get("description"))}
    if kind == "announcement":
        a = c.get(f"/api/v1/courses/{course_id}/discussion_topics/{item}")
        return {"kind": "announcement", "title": a.get("title"), "by": (a.get("author") or {}).get("display_name"),
                "at": a.get("posted_at"), "url": a.get("html_url"), "text": html_text(a.get("message"))}
    if kind == "page":
        p = c.get(f"/api/v1/courses/{course_id}/pages/{item}")
        return {"kind": "page", "title": p.get("title"), "updated": p.get("updated_at"), "url": p.get("html_url"), "text": html_text(p.get("body"))}
    raise InputError(f"unknown item kind {kind!r}")


def status(c: Canvas) -> dict:
    me = c.get("/api/v1/users/self")
    return {"canvas": c.base, "connected": True, "name": me.get("name"), "requests": c.requests}
