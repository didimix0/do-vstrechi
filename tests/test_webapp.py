from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib import error, request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from do_vstrechi.gigachat import ProviderError
from do_vstrechi.webapp import ApiError, TrainerApp, make_server


class FakeAnalyzer:
    def __init__(self, fail=False):
        self.calls = 0
        self.client_calls = 0
        self.fail = fail
        self.metrics = {"mode": "test", "status": "ok", "usage": {"prompt_tokens": 10}, "elapsed_seconds": 0}

    def analyze(self, session, text):
        self.calls += 1
        if self.fail:
            raise ProviderError("Проверочная ошибка сервиса")
        return {"actions": [{"code": "introduce", "quote": text}]}

    def generate_client_reply(self, session):
        self.client_calls += 1
        return "Здравствуйте. Да, встречу назначали, но я уже передумал. Мне своей карты вполне хватает."


class WebServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.fake = FakeAnalyzer()
        self.app = TrainerApp(Path(self.folder.name) / "web.sqlite3", lambda: self.fake)

    def tearDown(self):
        self.folder.cleanup()

    def turn(self, session, option):
        return self.app.mutate(session["id"], {"request_id": uuid.uuid4().hex,
                                               "revision": session["revision"], "option_id": option})

    def test_guided_call_and_review_do_not_claim_pass(self):
        session = self.app.create({"mode": "guided", "ending": "appointment"})
        for option in ("intro", "offer", "service", "benefit", "meeting", "transfers", "withdrawals", "both", "passport"):
            session = self.turn(session, option)
        self.assertEqual(session["status"], "ended")
        report = self.app.review(session["id"])
        self.assertEqual(report["training_verdict"], "not_evaluated")
        self.assertEqual(len(report["criteria"]), 10)
        self.assertEqual(self.fake.calls, 0)
        self.assertNotIn("materials", session)
        self.assertNotIn("ending_preference", session)
        self.assertNotIn("requests", session)

    def test_duplicate_request_is_not_applied_twice_even_concurrently(self):
        session = self.app.create({"mode": "gigachat", "ending": "appointment"})
        body = {"request_id": uuid.uuid4().hex, "revision": 0, "text": "Здравствуйте"}
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.app.mutate(session["id"], body), range(2)))
        self.assertEqual(self.fake.calls, 1)
        self.assertEqual(self.fake.client_calls, 1)
        self.assertEqual([r["turn_count"] for r in results], [1, 1])

    def test_failed_model_request_is_saved_and_not_recharged_on_same_id(self):
        self.fake.fail = True
        session = self.app.create({"mode": "gigachat", "ending": "appointment"})
        body = {"request_id": uuid.uuid4().hex, "revision": 0, "text": "Здравствуйте"}
        for _ in range(2):
            with self.assertRaises(ApiError) as captured:
                self.app.mutate(session["id"], body)
            self.assertEqual(captured.exception.status, 502)
            self.assertEqual(captured.exception.state["turn_count"], 0)
        self.assertEqual(self.fake.calls, 1)
        self.assertEqual(len(self.app.get(session["id"])["calls"]), 1)
        self.assertEqual(self.fake.client_calls, 0)

    def test_reusing_id_with_new_text_is_rejected(self):
        session = self.app.create({"mode": "gigachat", "ending": "appointment"})
        body = {"request_id": uuid.uuid4().hex, "revision": 0, "text": "Здравствуйте"}
        self.app.mutate(session["id"], body)
        with self.assertRaises(ApiError) as captured:
            self.app.mutate(session["id"], dict(body, text="Другая реплика"))
        self.assertEqual(captured.exception.status, 409)
        self.assertEqual(self.fake.calls, 1)

    def test_stop_is_control_event_and_does_not_invent_spoken_goodbye(self):
        session = self.app.create({"mode": "guided", "ending": "appointment"})
        session = self.app.mutate(session["id"], {"request_id": uuid.uuid4().hex, "revision": 0}, "finish")
        self.assertEqual(session["turn_count"], 0)
        self.assertEqual(session["history"][-1]["role"], "event")
        report = self.app.review(session["id"])
        self.assertTrue(report["evidence_incomplete"])
        self.assertFalse(any(c["evidence_candidates"] for c in report["criteria"]))

    def test_malformed_mode_and_stale_revision_are_rejected(self):
        with self.assertRaises(ApiError):
            self.app.create({"mode": [], "ending": "appointment"})
        session = self.app.create({"mode": "guided", "ending": "appointment"})
        self.turn(session, "intro")
        with self.assertRaises(ApiError) as captured:
            self.turn(session, "offer")
        self.assertEqual(captured.exception.status, 409)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.app = TrainerApp(Path(self.folder.name) / "web.sqlite3", lambda: FakeAnalyzer())
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.folder.cleanup()

    def fetch(self, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        default = {"Content-Type": "application/json"} if body is not None else {}
        req = request.Request(self.base + path, data=data, headers=default | (headers or {}))
        return request.urlopen(req, timeout=5)

    def test_assets_are_served_but_private_files_are_not(self):
        with self.fetch("/") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        for path in ("/.env", "/src/do_vstrechi/gigachat.py", "/../data/products/vtb_debit.v1.json"):
            with self.assertRaises(error.HTTPError) as captured:
                self.fetch(path)
            self.assertEqual(captured.exception.code, 404)

    def test_post_requires_token_and_rejects_cross_origin(self):
        body = {"mode": "guided", "ending": "appointment"}
        for headers in ({}, {"X-Trainer-Token": self.app.token, "Origin": "https://other.example"}):
            with self.assertRaises(error.HTTPError) as captured:
                self.fetch("/api/sessions", body, headers)
            self.assertEqual(captured.exception.code, 403)
        with self.fetch("/api/sessions", body, {"X-Trainer-Token": self.app.token, "Origin": self.base}) as response:
            session = json.load(response)
        with self.fetch("/api/sessions/" + session["id"]) as response:
            self.assertEqual(json.load(response)["mode"], "guided")

    def test_dns_rebinding_host_is_rejected(self):
        with self.assertRaises(error.HTTPError) as captured:
            self.fetch("/api/bootstrap", headers={"Host": "attacker.example"})
        self.assertEqual(captured.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
