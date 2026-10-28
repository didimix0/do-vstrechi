"""Локальное сохранение настроек без передачи ключа в чат или аргументы процесса."""
import base64
import binascii
import getpass
import json
import os
from pathlib import Path
import subprocess
import tempfile
import warnings
from .data import ROOT

DEFAULTS = {"GIGACHAT_SCOPE": "GIGACHAT_API_PERS", "GIGACHAT_MODEL": "GigaChat-2-Pro",
            "GIGACHAT_CA_BUNDLE": ""}


class DialogUnavailable(RuntimeError):
    """Окно недоступно; можно использовать защищённый ввод в терминале."""


def _read_windows_key(message: str = "") -> str | None:
    """Ключ возвращается по закрытому каналу процесса, без вывода в терминал."""
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        script = Path(__file__).with_name("key_dialog.ps1").read_text(encoding="utf-8")
        result = subprocess.run(
            [str(executable), "-NoProfile", "-STA", "-Command", script],
            input=json.dumps({"message": message}, ensure_ascii=False),
            capture_output=True, encoding="utf-8", errors="strict",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise DialogUnavailable
        reply = json.loads(result.stdout.lstrip("\ufeff"))
        if reply == {"status": "cancelled"}:
            return None
        if (isinstance(reply, dict) and set(reply) == {"status", "key"}
                and reply["status"] == "submitted" and isinstance(reply["key"], str)
                and len(reply["key"]) <= 4096):
            return reply["key"]
    except (OSError, UnicodeError, ValueError):
        pass
    # Вывод дочернего процесса может содержать ключ. Никогда не включаем его в ошибку.
    raise DialogUnavailable("Не удалось открыть окно настройки.") from None


def _read_terminal_key() -> str | None:
    print("Вставьте ключ. Символы не отображаются. Пустой Enter — отмена.")
    print("Если Ctrl+V не работает, используйте Ctrl+Shift+V или правую кнопку мыши.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            key = getpass.getpass("Authorization Key: ")
        except getpass.GetPassWarning:
            raise ValueError("Скрытый ввод недоступен. Откройте start.cmd в отдельном терминале.") from None
    return key if key.strip() else None


def save_authorization_key(path: Path, key: str) -> None:
    key = key.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise ValueError("Вместо ключа получен управляющий символ. Повторите вставку в поле Authorization Key.")
    try:
        decoded = base64.b64decode(key, validate=True).decode("utf-8")
    except (ValueError, binascii.Error, UnicodeError):
        raise ValueError("Неверный формат. Скопируйте полный Authorization Key из настроек API GigaChat; Client ID и Access token не подходят.") from None
    if not key or ":" not in decoded or any(c.isspace() for c in key):
        raise ValueError("Нужен полный Authorization Key, а не Client ID или Access token.")
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    updated = []
    found = set()
    for line in lines:
        name = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if name == "GIGACHAT_AUTH_KEY":
            if name not in found:
                updated.append(name + "=" + key)
            found.add(name)
        else:
            updated.append(line)
            if name:
                found.add(name)
    if "GIGACHAT_AUTH_KEY" not in found:
        updated.append("GIGACHAT_AUTH_KEY=" + key)
    for name, value in DEFAULTS.items():
        if name not in found:
            updated.append(name + "=" + value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".env.tmp-", delete=False) as file:
            temporary = Path(file.name)
            file.write("\n".join(updated) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def configure_local_environment(path: Path = ROOT / ".env", *, use_window: bool | None = None) -> None:
    print("Получите Authorization Key: https://developers.sber.ru/studio/workspaces/my-space/get/gigachat-api")
    use_dialog = os.name == "nt" if use_window is None else use_window
    message = ""
    if use_dialog:
        print("Открывается окно настройки. Вставьте ключ в поле и нажмите «Сохранить».")
    while True:
        try:
            key = _read_windows_key(message) if use_dialog else _read_terminal_key()
        except DialogUnavailable:
            print("Окно настройки недоступно. Используем скрытый ввод в терминале.")
            use_dialog = False
            continue
        if key is None:
            print("Настройка отменена, файл не изменён.")
            return
        try:
            save_authorization_key(path, key)
        except ValueError as exc:
            message = str(exc)
            print(message)
            continue
        except OSError:
            raise ValueError("Не удалось сохранить .env в папке проекта. Проверьте доступ к папке и повторите настройку.") from None
        os.environ["GIGACHAT_AUTH_KEY"] = key.strip()
        print("Ключ сохранён локально в .env. Теперь выберите пункт 2 — проверка API.")
        return
