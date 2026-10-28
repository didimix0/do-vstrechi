"""Один рабочий адрес и безопасный переход со старой закладки."""
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import socket
from urllib.request import build_opener,ProxyHandler
from .data import ROOT

DEFAULT_PORT=8766
LEGACY_PORT=8765
APP_ID='do-vstrechi'


class LocalHTTPServer(ThreadingHTTPServer):
    # SO_REUSEADDR в Windows позволяет двум серверам занять один порт.
    # На Unix он нужен для обычного перезапуска после закрытия соединений.
    allow_reuse_address=not hasattr(socket,'SO_EXCLUSIVEADDRUSE')
    def server_bind(self):
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


def runtime_revision(root=ROOT):
    paths=list((root/'web').glob('*'))+list((root/'src/do_vstrechi').glob('*.py'))
    paths+=list((root/'data/scenarios').glob('*.json'))+list((root/'prompts').glob('*.txt'))
    digest=hashlib.sha256()
    for path in sorted(p for p in paths if p.is_file()):
        digest.update(str(path.relative_to(root)).replace('\\','/').encode());digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def matching_server(address,revision):
    try:
        with build_opener(ProxyHandler({})).open(address+'/api/bootstrap',timeout=2) as response:
            return (response.status==200 and response.headers.get('X-Trainer-App')==APP_ID
                    and response.headers.get('X-Trainer-Revision')==revision)
    except (OSError,ValueError):
        return False


def make_legacy_redirect(target_port=DEFAULT_PORT,port=LEGACY_PORT):
    class Redirect(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def answer(self,status,payload=None,location=None):
            raw=json.dumps(payload,ensure_ascii=False).encode() if payload else b''
            self.send_response(status);self.send_header('Cache-Control','no-store')
            self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)))
            if location:self.send_header('Location',location)
            self.end_headers()
            if self.command!='HEAD':self.wfile.write(raw)
        def allowed(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        def do_GET(self):
            if not self.allowed():self.answer(403,{'error':'Доступ разрешён только по локальному адресу.'});return
            path=self.path if self.path.startswith('/') and not self.path.startswith('//') else '/'
            self.answer(302,location=f'http://127.0.0.1:{target_port}'+path)
        do_HEAD=do_GET
        def do_POST(self):
            size=self.headers.get('Content-Length','')
            if size.isdigit() and 0<int(size)<=1_500_000:
                try:
                    self.connection.settimeout(1);self.rfile.read(int(size))
                except OSError:pass
                finally:self.connection.settimeout(None)
            self.answer(409 if self.allowed() else 403,{'error':f'Адрес тренажёра обновлён: http://127.0.0.1:{target_port}. Обновите страницу. Отправка не выполнена.'})
    return LocalHTTPServer(('127.0.0.1',port),Redirect)
