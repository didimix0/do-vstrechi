"""Временный доступ для команды. Отдельная БД и CSRF-токен для каждого браузера."""
from collections import deque
from http.cookies import SimpleCookie, CookieError
import json
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from .data import ROOT
from .voice_input import VoiceRecognizer
from .webapp import ApiError, TrainerApp

COOKIE = 'dv_team_visit'
VISIT_SECONDS = 24 * 3600


class TeamAccess:
    def __init__(self, folder: Path, code: str, *, app_factory=TrainerApp,
                 voice=None, max_visitors=32, max_parallel=2):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.code = code
        self.public_origin = ''
        self.app_factory = app_factory
        self.voice = voice or VoiceRecognizer()
        self.max_visitors = max_visitors
        self.visitors = {}
        self.failures = deque()
        self.lock = threading.RLock()
        self.slots = threading.BoundedSemaphore(max_parallel)

    def set_origin(self, origin):
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or parsed.port is not None or parsed.path not in {'', '/'}
                or parsed.query or parsed.fragment or parsed.username or parsed.password
                or not re.fullmatch(r'[a-z0-9-]+\.trycloudflare\.com', parsed.hostname or '')):
            raise ValueError('Ожидается точный HTTPS-адрес временной ссылки Cloudflare.')
        self.public_origin = 'https://' + parsed.netloc

    def request_origin(self, handler):
        port = handler.server.server_port
        local = {f'127.0.0.1:{port}', f'localhost:{port}'}
        host = handler.headers.get('Host', '')
        if host in local:
            return 'http://' + host
        if self.public_origin and host == urlsplit(self.public_origin).netloc:
            return self.public_origin
        raise ApiError(403, 'Адрес командной проверки не совпадает. Откройте полученную ссылку.')

    def check_request(self, handler):
        handler.connection.settimeout(30)
        origin = self.request_origin(handler)
        if handler.command == 'POST' and handler.headers.get('Origin') != origin:
            raise ApiError(403, 'Недопустимый источник запроса. Откройте страницу тренажёра.')
        if handler.headers.get('Transfer-Encoding'):
            raise ApiError(400, 'Этот формат передачи не поддерживается.')
        return origin

    def visitor(self, handler):
        try:
            cookies = SimpleCookie()
            cookies.load(handler.headers.get('Cookie', ''))
            token = cookies[COOKIE].value if COOKIE in cookies else ''
        except CookieError:
            return None
        with self.lock:
            visit = self.visitors.get(token)
            if visit and visit['expires'] > time.monotonic():
                return visit['app']
        return None

    def handle(self, handler):
        origin = self.check_request(handler)
        path = urlsplit(handler.path).path
        if handler.command == 'GET' and path in {'/team-login', '/team-login.js', '/team-login.css'}:
            name, mime = {'/team-login': ('team-login.html', 'text/html'),
                          '/team-login.js': ('team-login.js', 'text/javascript'),
                          '/team-login.css': ('team-login.css', 'text/css')}[path]
            handler.respond(200, (ROOT / 'web' / name).read_bytes(), mime)
            return True
        if handler.command == 'POST' and path == '/api/team/login':
            self.login(handler, origin)
            return True
        if self.visitor(handler) is None:
            if handler.command == 'GET' and not path.startswith('/api/'):
                handler.respond(302, b'', 'text/plain', {'Location': '/team-login'})
                return True
            raise ApiError(401, 'Нужен код доступа команды. Обновите страницу и войдите снова.')
        return False

    def login(self, handler, origin):
        size = handler.headers.get('Content-Length', '')
        if (not size.isdigit() or not 1 <= int(size) <= 512
                or handler.headers.get('Content-Type', '').split(';')[0] != 'application/json'):
            raise ApiError(400, 'Введите код доступа.')
        try:
            body = json.loads(handler.rfile.read(int(size)))
        except (ValueError, UnicodeError):
            raise ApiError(400, 'Введите код доступа.') from None
        supplied = body.get('code') if isinstance(body, dict) else None
        with self.lock:
            now = time.monotonic()
            while self.failures and self.failures[0] < now - 60:
                self.failures.popleft()
            if len(self.failures) >= 20:
                handler.respond(429, {'error': 'Слишком много попыток входа. Подождите минуту.'})
                return
            if not isinstance(supplied, str) or not secrets.compare_digest(supplied.strip().encode(), self.code.encode()):
                self.failures.append(now)
                handler.respond(401, {'error': 'Код не подошёл. Проверьте его в сообщении команды.'})
                return
            # Повторный вход в том же браузере сохраняет его собственную историю.
            existing = self.visitor(handler)
            if existing:
                handler.respond(200, {'ok': True})
                return
            self.visitors = {k: v for k, v in self.visitors.items() if v['expires'] > now}
            if len(self.visitors) >= self.max_visitors:
                handler.respond(429, {'error': 'Лимит браузеров для этой проверки исчерпан. Сообщите организатору.'})
                return
            token = secrets.token_urlsafe(32)
            visitor_app = self.app_factory(self.folder / (secrets.token_hex(16) + '.sqlite3'))
            # Одна модель Vosk на весь процесс, а не новая копия на каждого посетителя.
            visitor_app.voice = self.voice
            self.visitors[token] = {'app': visitor_app, 'expires': now + VISIT_SECONDS}
        cookie = SimpleCookie()
        cookie[COOKIE] = token
        cookie[COOKIE]['path'] = '/'
        cookie[COOKIE]['httponly'] = True
        cookie[COOKIE]['samesite'] = 'Strict'
        cookie[COOKIE]['max-age'] = VISIT_SECONDS
        if origin.startswith('https:'):
            cookie[COOKIE]['secure'] = True
        handler.respond(200, {'ok': True}, headers={'Set-Cookie': cookie.output(header='').strip()})

    def guard(self, handler):
        self.check_request(handler)
        app = self.visitor(handler)
        if app is None:
            raise ApiError(401, 'Сессия командной проверки закончилась. Обновите страницу.')
        if handler.command == 'POST':
            if not secrets.compare_digest(handler.headers.get('X-Trainer-Token', ''), app.token):
                raise ApiError(403, 'Обновите страницу тренажёра.')
            path = urlsplit(handler.path).path
            if path.endswith(('/turns', '/evaluate', '/transcribe')):
                if not self.slots.acquire(blocking=False):
                    raise ApiError(429, 'Сейчас обрабатываются ответы коллег. Повторите через несколько секунд.')
                handler.team_slot = True
        return app

    def release(self, handler):
        if getattr(handler, 'team_slot', False):
            handler.team_slot = False
            self.slots.release()
