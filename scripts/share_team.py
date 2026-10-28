"""Запуск временной HTTPS-ссылки. Ключ GigaChat остаётся в локальном .env."""
import argparse
import json
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from do_vstrechi.gigachat import load_local_environment
from do_vstrechi.team_access import TeamAccess
from do_vstrechi.webapp import TrainerApp, make_server


def main():
    parser = argparse.ArgumentParser(description='Временный доступ к тренажёру для команды')
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    load_local_environment()
    private = ROOT / 'var' / 'team-review'
    private.mkdir(parents=True, exist_ok=True)
    settings_path = private / 'settings.json'
    if settings_path.exists():
        code = json.loads(settings_path.read_text(encoding='utf-8'))['access_code']
    else:
        code = 'MEET-' + secrets.token_hex(8).upper()
        settings_path.write_text(json.dumps({'access_code': code}), encoding='utf-8')
    executable = ROOT / 'var' / 'tools' / 'cloudflared.exe'
    if not executable.is_file():
        raise ValueError('Не найден Cloudflare Tunnel в var/tools/cloudflared.exe.')
    run_folder = private / ('run-' + time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3))
    access = TeamAccess(run_folder, code)
    template = TrainerApp(run_folder / 'server.sqlite3')
    server = make_server(template, args.port, access=access)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log = open(private / 'tunnel.log', 'w', encoding='utf-8')
    process = None
    try:
        process = subprocess.Popen(
            [str(executable), '--no-autoupdate', 'tunnel', '--url',
             f'http://127.0.0.1:{server.server_port}', '--protocol', 'http2'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        events = queue.Queue()

        def read_log():
            for line in process.stdout:
                log.write(line)
                log.flush()
                found = re.search(r'https://(?!api\.)([a-z0-9-]+)\.trycloudflare\.com', line)
                if found:
                    events.put(('url', found.group(0)))
                if 'Registered tunnel connection' in line:
                    events.put(('connected', True))
            events.put(('ended', process.poll()))

        reader = threading.Thread(target=read_log, daemon=True)
        reader.start()
        print('Готовим временную ссылку. Локальный тренажёр продолжает работать отдельно.', flush=True)
        deadline = time.monotonic() + 75
        public_url = ''
        connected = False
        while time.monotonic() < deadline and not (public_url and connected):
            try:
                kind, value = events.get(timeout=1)
            except queue.Empty:
                continue
            if kind == 'url':
                public_url = value
                access.set_origin(public_url)
            elif kind == 'connected':
                connected = True
            elif kind == 'ended':
                raise ValueError('Не удалось открыть туннель. Подробности: var/team-review/tunnel.log')
        if not public_url or not connected:
            raise ValueError('Не удалось дождаться подключения. Подробности: var/team-review/tunnel.log')
        state = {'url': public_url, 'access_code': code, 'server_pid': os.getpid(),
                 'tunnel_pid': process.pid, 'port': server.server_port,
                 'started_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'status': 'running'}
        state_path = private / 'current.json'
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        print('ССЫЛКА ДЛЯ КОМАНДЫ: ' + public_url, flush=True)
        print('КОД ДОСТУПА: ' + code, flush=True)
        print('Компьютер и это подключение должны оставаться включены. Остановка: Ctrl+C.', flush=True)
        process.wait()
    except KeyboardInterrupt:
        print('Командная проверка остановлена.', flush=True)
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        log.close()
        state_path = private / 'current.json'
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding='utf-8'))
            if state.get('server_pid') == os.getpid():
                state['status'] = 'stopped'
                state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
