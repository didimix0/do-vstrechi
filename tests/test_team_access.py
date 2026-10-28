import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from do_vstrechi.team_access import COOKIE, TeamAccess
from do_vstrechi.webapp import TrainerApp, make_server
from test_webapp import FakeAnalyzer


class FakeVoice:
    def status(self):
        return {'ready': True, 'engine': 'Vosk'}

    def recognize(self, raw):
        return {'text': 'Здравствуйте', 'engine': 'Vosk', 'editable': True}


class TeamAccessTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        factory = lambda path: TrainerApp(path, lambda: FakeAnalyzer())
        self.access = TeamAccess(Path(self.folder.name), 'TEST-TEAM-CODE',
                                 app_factory=factory, voice=FakeVoice(), max_parallel=1)
        self.access.set_origin('https://team-test.trycloudflare.com')
        self.app = factory(Path(self.folder.name) / 'local.sqlite3')
        self.local_session = self.app.create({'mode': 'guided', 'ending': 'appointment'})
        self.server = make_server(self.app, 0, access=self.access)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.folder.cleanup()

    def fetch(self, path, body=None, cookie=None, token=None, headers=None):
        values = {}
        if body is not None:
            values = {'Content-Type': 'application/json', 'Origin': self.origin}
        if cookie:
            values['Cookie'] = cookie
        if token:
            values['X-Trainer-Token'] = token
        values.update(headers or {})
        raw = json.dumps(body).encode() if body is not None else None
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('POST' if body is not None else 'GET', path, raw, values)
            response = connection.getresponse()
            result = response.read()
            return response.status, dict(response.getheaders()), (
                json.loads(result) if response.getheader('Content-Type', '').startswith('application/json') else result)
        finally:
            connection.close()

    def login(self):
        status, headers, body = self.fetch('/api/team/login', {'code': 'TEST-TEAM-CODE'})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie'].split(';')[0]
        status, _, bootstrap = self.fetch('/api/bootstrap', cookie=cookie)
        self.assertEqual(status, 200)
        return cookie, bootstrap['token']

    def test_unauthenticated_visitors_see_login_and_no_api_or_local_history(self):
        status, headers, _ = self.fetch('/')
        self.assertEqual(status, 302)
        self.assertEqual(headers['Location'], '/team-login')
        self.assertEqual(self.fetch('/team-login')[0], 200)
        self.assertEqual(self.fetch('/team-login.js')[0], 200)
        for path in ('/api/bootstrap', '/api/sessions', '/api/sessions/' + self.local_session['id']):
            self.assertEqual(self.fetch(path)[0], 401)

    def test_login_code_and_origin_are_both_required_and_failures_are_throttled(self):
        self.assertEqual(self.fetch('/api/team/login', {'code': 'wrong'})[0], 401)
        self.assertEqual(self.fetch('/api/team/login', {'code': 'TEST-TEAM-CODE'},
                                    headers={'Origin': 'https://attacker.example'})[0], 403)
        for _ in range(19):
            self.assertEqual(self.fetch('/api/team/login', {'code': 'wrong'})[0], 401)
        self.assertEqual(self.fetch('/api/team/login', {'code': 'wrong'})[0], 429)

    def test_cookie_is_secure_httponly_and_not_exposed_in_json(self):
        status, headers, body = self.fetch('/api/team/login', {'code': 'TEST-TEAM-CODE'}, headers={
            'Host': 'team-test.trycloudflare.com', 'Origin': self.access.public_origin})
        self.assertEqual(status, 200)
        for flag in ('HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/'):
            self.assertIn(flag, headers['Set-Cookie'])
        self.assertEqual(body, {'ok': True})

    def test_each_browser_has_own_history_and_csrf_token(self):
        cookie_a, token_a = self.login()
        cookie_b, token_b = self.login()
        self.assertNotEqual(cookie_a, cookie_b)
        self.assertNotEqual(token_a, token_b)
        _, _, session = self.fetch('/api/sessions', {'mode': 'guided', 'ending': 'appointment'}, cookie_a, token_a)
        self.assertEqual(self.fetch('/api/sessions', cookie=cookie_b)[2], [])
        self.assertEqual(self.fetch('/api/sessions/' + session['id'], cookie=cookie_b)[0], 404)
        self.assertEqual(self.fetch('/api/sessions/' + self.local_session['id'], cookie=cookie_a)[0], 404)
        self.assertEqual(self.fetch('/api/sessions', {'mode': 'guided', 'ending': 'appointment'}, cookie_b, token_a)[0], 403)
        status, _, items = self.fetch('/api/sessions', cookie=cookie_a)
        self.assertEqual(status, 200)
        self.assertEqual([item['id'] for item in items], [session['id']])
        self.assertEqual(self.fetch('/api/bootstrap', cookie=cookie_a)[2]['token'], token_a)

    def test_private_files_and_invalid_public_hosts_remain_blocked(self):
        cookie, _ = self.login()
        for path in ('/.env', '/var/team-review/settings.json', '/src/do_vstrechi/gigachat.py'):
            self.assertEqual(self.fetch(path, cookie=cookie)[0], 404)
        self.assertEqual(self.fetch('/api/bootstrap', cookie=cookie, headers={'Host': 'attacker.example'})[0], 403)
        self.assertEqual(self.fetch('/api/bootstrap', cookie=cookie, headers={'Host': 'other.trycloudflare.com'})[0], 403)

    def test_guided_dialogue_and_report_work_through_the_same_gate(self):
        cookie, token = self.login()
        _, _, session = self.fetch('/api/sessions', {'mode': 'guided', 'ending': 'appointment'}, cookie, token)
        for option in ('intro', 'offer', 'service', 'benefit', 'meeting', 'transfers', 'withdrawals', 'both', 'passport'):
            status, _, session = self.fetch('/api/sessions/' + session['id'] + '/turns',
                {'request_id': uuid.uuid4().hex, 'revision': session['revision'], 'option_id': option}, cookie, token)
            self.assertEqual(status, 200)
        self.assertEqual(session['status'], 'ended')
        status, _, report = self.fetch('/api/sessions/' + session['id'] + '/review', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(len(report['criteria']), 10)

    def test_busy_response_does_not_change_session_and_slot_is_released(self):
        cookie, token = self.login()
        _, _, session = self.fetch('/api/sessions', {'mode': 'guided', 'ending': 'appointment'}, cookie, token)
        body = {'request_id': uuid.uuid4().hex, 'revision': 0, 'option_id': 'intro'}
        self.access.slots.acquire()
        try:
            self.assertEqual(self.fetch('/api/sessions/' + session['id'] + '/turns', body, cookie, token)[0], 429)
        finally:
            self.access.slots.release()
        self.assertEqual(self.fetch('/api/sessions/' + session['id'], cookie=cookie)[2]['revision'], 0)
        self.assertEqual(self.fetch('/api/sessions/' + session['id'] + '/turns', body, cookie, token)[0], 200)
        self.assertTrue(self.access.slots.acquire(blocking=False))
        self.access.slots.release()

    def test_expired_cookie_does_not_expose_previous_browser_history(self):
        cookie, _ = self.login()
        cookie_token = cookie.split('=', 1)[1]
        self.access.visitors[cookie_token]['expires'] = time.monotonic() - 1
        self.assertEqual(self.fetch('/api/bootstrap', cookie=cookie)[0], 401)
        self.assertEqual(self.fetch('/', cookie=cookie)[0], 302)

    def test_public_origin_accepts_only_exact_quick_tunnel_https_address(self):
        for value in ('http://test.trycloudflare.com', 'https://attacker.example',
                      'https://test.trycloudflare.com/another', 'https://user@test.trycloudflare.com',
                      'https://test.trycloudflare.com:443'):
            with self.assertRaises(ValueError):
                self.access.set_origin(value)


if __name__ == '__main__':
    unittest.main()
