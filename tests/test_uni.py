"""Tests against a fake Canvas on 127.0.0.1. Run: python3 -m unittest discover -s tests"""

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from uni_cli import commands  # noqa: E402
from uni_cli.canvas import Canvas, CanvasError, ReadOnlyError, check_base_url  # noqa: E402
from uni_cli.cli import main  # noqa: E402
from uni_cli.config import ConfigError, load  # noqa: E402

TOKEN = "test-token-not-real-0000"
NOW = datetime.now(timezone.utc)


def at(hours: float) -> str:
    return (NOW + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


COURSE = {"id": 1001, "course_code": "BIO120", "name": "Biology", "term": {"name": "Fall"},
          "enrollments": [{"type": "student", "computed_current_score": 88.5, "computed_current_grade": "A-", "computed_final_score": 60.1}]}
ASSIGNMENTS = [
    {"id": 1, "name": "Lab 1", "created_at": at(-200), "updated_at": at(-200), "due_at": at(-100), "points_possible": 10,
     "html_url": "/courses/1001/assignments/1", "submission": {"score": 9, "graded_at": at(-2), "posted_at": at(-2)}},
    {"id": 2, "name": "Lab 2", "created_at": at(-1), "updated_at": at(-1), "due_at": at(48), "points_possible": 10,
     "html_url": "/courses/1001/assignments/2", "submission": {"score": None}},
    {"id": 3, "name": "Essay", "created_at": at(-300), "updated_at": at(-3), "due_at": at(72), "points_possible": 20,
     "html_url": "/courses/1001/assignments/3", "submission": {}},
]


class FakeCanvas(BaseHTTPRequestHandler):
    routes: dict = {}
    seen: list = []

    def log_message(self, *args) -> None:
        pass

    def _answer(self) -> None:
        parts = urlsplit(self.path)
        type(self).seen.append((self.command, parts.path, self.headers.get("Authorization")))
        status, body, headers = self.routes.get(parts.path, (404, {"errors": [{"message": "not found"}]}, {}))
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    do_GET = do_POST = do_PUT = do_DELETE = _answer


class UniTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCanvas)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        FakeCanvas.seen = []
        FakeCanvas.routes = {
            "/api/v1/users/self": (200, {"id": 7, "name": "Test Student"}, {}),
            "/api/v1/courses": (200, [COURSE], {}),
            "/api/v1/courses/1001/tabs": (200, [{"type": "external", "label": "Gradebook Tool", "html_url": "/courses/1001/external_tools/5"},
                                               {"type": "internal", "label": "Home", "html_url": "/courses/1001"}], {}),
            "/api/v1/planner/items": (200, [{"context_name": "Biology", "plannable_type": "assignment", "html_url": "/courses/1001/assignments/2",
                                             "plannable": {"title": "Lab 2", "due_at": at(48), "points_possible": 10},
                                             "submissions": {"submitted": False}}], {}),
            "/api/v1/users/self/missing_submissions": (200, [], {}),
            "/api/v1/courses/1001/assignments": (200, ASSIGNMENTS, {}),
            "/api/v1/announcements": (200, [{"id": 55, "context_code": "course_1001", "title": "Room change", "posted_at": at(-5),
                                             "author": {"display_name": "Prof. Lee"}, "message": "<p>We meet in <b>Room 2</b>.</p>",
                                             "html_url": f"{self.base}/courses/1001/discussion_topics/55"}], {}),
            "/api/v1/courses/1001/files": (403, {"status": "unauthorized"}, {}),
            "/api/v1/courses/1001/pages": (200, [{"url": "week-3", "title": "Week 3", "created_at": at(-4), "html_url": f"{self.base}/courses/1001/pages/week-3"}], {}),
            "/api/v1/courses/1001/assignments/2": (200, ASSIGNMENTS[1] | {"description": "<p>Write it up.</p>"}, {}),
            "/api/v1/courses/1001/discussion_topics/55": (200, {"title": "Room change", "message": "<p>Room 2</p>"}, {}),
            "/api/v1/courses/1001/pages/week-3": (200, {"title": "Week 3", "body": "<h2>Plan</h2><p>Read chapter 3.</p>"}, {}),
        }
        self.canvas = Canvas(self.base, TOKEN)

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        env = {"CANVAS_BASE_URL": self.base, "CANVAS_TOKEN": TOKEN, "UNI_CONFIG_DIR": "/nonexistent"}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()


class ReadOnlyTests(UniTest):
    def test_every_command_only_reads_and_never_touches_the_inbox_or_quiz_questions(self) -> None:
        for argv in (["status"], ["courses"], ["due"], ["grades"], ["grades", "bio"], ["announcements"], ["changes", "--since", "24h"],
                     ["show", "assignment:1001:2"], ["show", "announcement:1001:55"], ["show", "page:1001:week-3"]):
            code, out, err = self.run_cli(*argv, "--json")
            self.assertEqual(code, 0, f"{argv}: {err}")
            json.loads(out)
        self.assertTrue(FakeCanvas.seen)
        for method, path, auth in FakeCanvas.seen:
            self.assertEqual(method, "GET")
            self.assertTrue(path.startswith("/api/v1/"))
            self.assertNotIn("conversations", path)
            self.assertNotRegex(path, r"quiz")
            self.assertEqual(auth, f"Bearer {TOKEN}")

    def test_writes_other_hosts_the_inbox_and_quiz_questions_are_refused_before_sending(self) -> None:
        for method, url in (("POST", f"{self.base}/api/v1/courses"), ("DELETE", f"{self.base}/api/v1/courses/1"),
                            ("GET", "https://elsewhere.invalid/api/v1/courses"), ("GET", f"{self.base}/api/v1/conversations"),
                            ("GET", f"{self.base}/api/v1/conversations/unread_count"),
                            ("GET", f"{self.base}/api/v1/courses/1/quizzes/2/questions"),
                            ("GET", f"{self.base}/api/v1/courses/1/quizzes/2/submissions"), ("GET", f"{self.base}/login")):
            with self.assertRaises(ReadOnlyError, msg=url):
                self.canvas._guard(method, url)
        with self.assertRaises(ReadOnlyError):
            self.canvas.get("/api/v1/conversations")
        self.assertEqual(FakeCanvas.seen, [])

    def test_pagination_stays_on_the_canvas_host(self) -> None:
        FakeCanvas.routes["/api/v1/courses"] = (200, [COURSE], {"Link": '<https://elsewhere.invalid/api/v1/courses?page=2>; rel="next"'})
        with self.assertRaises(ReadOnlyError):
            self.canvas.get_all("/api/v1/courses")
        FakeCanvas.routes["/api/v1/courses"] = (200, [COURSE], {"Link": f'<{self.base}/api/v1/courses/page2>; rel="next"'})
        FakeCanvas.routes["/api/v1/courses/page2"] = (200, [COURSE | {"id": 1002}], {})
        self.assertEqual([c["id"] for c in self.canvas.get_all("/api/v1/courses")], [1001, 1002])

    def test_a_redirect_is_never_followed_so_the_token_never_leaves(self) -> None:
        FakeCanvas.routes["/api/v1/users/self"] = (302, {}, {"Location": "https://elsewhere.invalid/steal"})
        with self.assertRaises(CanvasError):
            self.canvas.get("/api/v1/users/self")

    def test_the_token_never_appears_in_output_or_errors(self) -> None:
        FakeCanvas.routes["/api/v1/users/self"] = (401, {"errors": [{"message": f"bad token {TOKEN}"}]}, {})
        code, out, err = self.run_cli("status")
        self.assertEqual(code, 1)
        self.assertNotIn(TOKEN, out + err)
        _, out, err = self.run_cli("courses", "--json")
        self.assertNotIn(TOKEN, out + err)


class CommandTests(UniTest):
    def test_courses_due_and_grades(self) -> None:
        courses = commands.courses(self.canvas)["courses"]
        self.assertEqual(courses[0]["code"], "BIO120")
        self.assertEqual(courses[0]["tools"], [{"label": "Gradebook Tool", "url": f"{self.base}/courses/1001/external_tools/5"}])
        due = commands.due(self.canvas, 7)
        self.assertEqual([(i["title"], i["status"], i["url"]) for i in due["items"]], [("Lab 2", "not submitted", f"{self.base}/courses/1001/assignments/2")])
        grades = commands.grades(self.canvas, "BIO")["courses"][0]
        self.assertEqual((grades["currentScore"], grades["currentGrade"]), (88.5, "A-"))
        self.assertEqual([(m["title"], m["score"], m["outOf"]) for m in grades["marks"]], [("Lab 1", 9, 10)])

    def test_changes_lists_new_work_marks_updates_and_posts_after_a_time_oldest_first_with_a_cursor(self) -> None:
        feed = commands.changes(self.canvas, "24h")
        self.assertEqual([(i["kind"], i["title"]) for i in feed["items"]],
                         [("announcement", "Room change"), ("page", "Week 3"), ("deadline", "Essay"), ("grade", "Lab 1"), ("assignment", "Lab 2")])
        self.assertEqual(feed["items"][3]["summary"], "Graded: 9/10")
        cursor = datetime.fromisoformat(feed["cursor"])
        self.assertLess(abs(cursor - datetime.now(timezone.utc)), timedelta(minutes=1))
        self.assertEqual(commands.changes(self.canvas, feed["cursor"])["items"], [])
        self.assertEqual([i["title"] for i in commands.changes(self.canvas, "90m")["items"]], ["Lab 2"])

    def test_a_course_that_hides_its_files_still_lists_announcements_and_pages(self) -> None:
        posts = commands.announcements(self.canvas, since="1d")["items"]
        self.assertEqual([(p["kind"], p["title"], p["by"]) for p in posts], [("page", "Week 3", None), ("announcement", "Room change", "Prof. Lee")])
        self.assertEqual(posts[1]["text"], "We meet in Room 2.")

    def test_show_gives_one_item_in_full(self) -> None:
        self.assertEqual(commands.show(self.canvas, "assignment:1001:2")["description"], "Write it up.")
        self.assertEqual(commands.show(self.canvas, "page:1001:week-3")["text"], "Plan\nRead chapter 3.")
        with self.assertRaises(commands.InputError):
            commands.show(self.canvas, "quiz:1001:4")

    def test_since_accepts_spans_dates_and_instants(self) -> None:
        base = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(commands.parse_since("90m", base), base - timedelta(minutes=90))
        self.assertEqual(commands.parse_since("2d", base), base - timedelta(days=2))
        self.assertEqual(commands.parse_since("2026-01-09T08:00:00Z"), datetime(2026, 1, 9, 8, tzinfo=timezone.utc))
        with self.assertRaises(commands.InputError):
            commands.parse_since("yesterday")


class ConfigTests(unittest.TestCase):
    def test_the_canvas_address_must_be_https_and_just_the_site(self) -> None:
        self.assertEqual(check_base_url("https://canvas.instructure.com/"), "https://canvas.instructure.com")
        for bad in ("http://canvas.instructure.com", "https://canvas.instructure.com/courses", "https://user:pw@canvas.instructure.com", "canvas"):
            with self.assertRaises(ValueError, msg=bad):
                check_base_url(bad)

    def test_a_token_file_others_can_read_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            token = Path(folder) / "token"
            token.write_text(TOKEN)
            token.chmod(0o644)
            env = {"CANVAS_BASE_URL": "https://canvas.instructure.com", "CANVAS_TOKEN_FILE": str(token), "UNI_CONFIG_DIR": folder}
            with mock.patch.dict(os.environ, env):
                os.environ.pop("CANVAS_TOKEN", None)
                with self.assertRaises(ConfigError):
                    load()
                token.chmod(0o600)
                self.assertEqual(load().base, "https://canvas.instructure.com")


if __name__ == "__main__":
    unittest.main()
