import base64
from contextlib import redirect_stdout
import getpass
import io
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib import error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from do_vstrechi.configure import DialogUnavailable, _read_windows_key, configure_local_environment, save_authorization_key
from do_vstrechi.connection import check_connection
from do_vstrechi.contracts import ContractError
from legacy_fixtures import load_bundle
from do_vstrechi.engine import new_session
from do_vstrechi.gigachat import GigaChatAnalyzer, ProviderError


class FakeConnection(GigaChatAnalyzer):
    def __init__(self, responses):
        super().__init__({"GIGACHAT_AUTH_KEY": "test-only-key"})
        self.responses = iter(responses)
        self.methods = []

    def _post(self, url, body, headers):
        self.methods.append("POST")
        return next(self.responses)

    def _get(self, url, headers):
        self.methods.append("GET")
        return next(self.responses)


def token():
    return {"access_token": "test-token", "expires_at": int((time.time() + 1800) * 1000)}


class ConfigurationTests(unittest.TestCase):
    def test_saving_key_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("# local\nGIGACHAT_MODEL=GigaChat-2-Max\nOTHER=keep\nGIGACHAT_AUTH_KEY=old\n", encoding="utf-8")
            key = base64.b64encode(b"test-client:test-secret").decode()
            save_authorization_key(path, key)
            content = path.read_text(encoding="utf-8")
            self.assertIn("GIGACHAT_AUTH_KEY=" + key, content)
            self.assertIn("GIGACHAT_MODEL=GigaChat-2-Max", content)
            self.assertIn("OTHER=keep", content)
            self.assertEqual(content.count("GIGACHAT_AUTH_KEY="), 1)
            self.assertFalse(list(path.parent.glob(".env.tmp-*")))

    def test_invalid_key_does_not_overwrite_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("OTHER=keep\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                save_authorization_key(path, "not-an-authorization-key")
            self.assertEqual(path.read_text(encoding="utf-8"), "OTHER=keep\n")

    def test_hidden_input_failure_does_not_fall_back_to_echo(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            path = Path(folder) / ".env"
            with patch("do_vstrechi.configure.getpass.getpass", side_effect=getpass.GetPassWarning):
                with self.assertRaises(ValueError):
                    configure_local_environment(path, use_window=False)
            self.assertFalse(path.exists())

    def test_dialog_saves_pasted_key_without_echo(self):
        key = base64.b64encode(b"test-client:test-secret").decode()
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(output), patch.dict(os.environ):
            path = Path(folder) / ".env"
            with patch("do_vstrechi.configure._read_windows_key", return_value=key):
                configure_local_environment(path, use_window=True)
            self.assertIn("GIGACHAT_AUTH_KEY=" + key, path.read_text(encoding="utf-8"))
            self.assertEqual(os.environ["GIGACHAT_AUTH_KEY"], key)
        self.assertNotIn(key, output.getvalue())

    def test_invalid_dialog_submission_can_be_corrected(self):
        key = base64.b64encode(b"test-client:test-secret").decode()
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()), patch.dict(os.environ):
            path = Path(folder) / ".env"
            with patch("do_vstrechi.configure._read_windows_key", side_effect=["bad-key", key]) as dialog:
                configure_local_environment(path, use_window=True)
            self.assertIn("Неверный формат", dialog.call_args_list[1].args[0])
            self.assertIn(key, path.read_text(encoding="utf-8"))

    def test_cancel_after_bad_key_preserves_file_and_environment(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()), patch.dict(os.environ, {"GIGACHAT_AUTH_KEY": "old"}):
            path = Path(folder) / ".env"
            path.write_text("OTHER=keep\n", encoding="utf-8")
            with patch("do_vstrechi.configure._read_windows_key", side_effect=["bad-key", None]):
                configure_local_environment(path, use_window=True)
            self.assertEqual(path.read_text(encoding="utf-8"), "OTHER=keep\n")
            self.assertEqual(os.environ["GIGACHAT_AUTH_KEY"], "old")

    def test_missing_dialog_uses_secure_terminal_input(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            path = Path(folder) / ".env"
            with patch("do_vstrechi.configure._read_windows_key", side_effect=DialogUnavailable), patch(
                    "do_vstrechi.configure.getpass.getpass", return_value="") as secure_input:
                configure_local_environment(path, use_window=True)
            secure_input.assert_called_once()
            self.assertFalse(path.exists())

    def test_dialog_transport_keeps_key_out_of_arguments_and_errors(self):
        key = base64.b64encode(b"test-client:test-secret").decode()
        process = SimpleNamespace(returncode=0, stdout=json.dumps({"status": "submitted", "key": key}))
        with patch("do_vstrechi.configure.subprocess.run", return_value=process) as run:
            self.assertEqual(_read_windows_key(), key)
            self.assertNotIn(key, repr(run.call_args))
            self.assertTrue(run.call_args.kwargs["capture_output"])
        with patch("do_vstrechi.configure.subprocess.run", return_value=SimpleNamespace(returncode=1, stdout=key)):
            with self.assertRaises(DialogUnavailable) as captured:
                _read_windows_key()
            self.assertNotIn(key, str(captured.exception))

    def test_control_character_has_paste_hint_without_echo(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError) as captured:
                save_authorization_key(Path(folder) / ".env", "\x16")
            self.assertIn("Повторите вставку", str(captured.exception))
            self.assertNotIn("\x16", str(captured.exception))

    def test_write_failure_is_explained_without_echo(self):
        key = base64.b64encode(b"test-client:test-secret").decode()
        with redirect_stdout(io.StringIO()), patch("do_vstrechi.configure._read_windows_key", return_value=key), patch(
                "do_vstrechi.configure.save_authorization_key", side_effect=PermissionError(key)):
            with self.assertRaises(ValueError) as captured:
                configure_local_environment(Path("unused"), use_window=True)
            self.assertIn("Не удалось сохранить", str(captured.exception))
            self.assertNotIn(key, str(captured.exception))

    @unittest.skipUnless(os.name == "nt", "Окно использует компоненты Windows")
    def test_native_dialog_controls_without_showing_window(self):
        script = (Path(__file__).resolve().parents[1] / "src/do_vstrechi/key_dialog.ps1").read_text(encoding="utf-8")
        executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run(
            [str(executable), "-NoProfile", "-STA", "-Command", "& {\n" + script + "\n} -VerifyOnly"],
            input='{"message":""}', capture_output=True, encoding="utf-8", timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout.lstrip("\ufeff"))
        self.assertEqual(report["status"], "verified")
        self.assertTrue(report["masked"])
        self.assertTrue(report["save_enabled"])
        self.assertIn("9", report["count"])
        self.assertFalse(report["visible"])


class ConnectionTests(unittest.TestCase):
    def test_single_probe_checks_access_and_contract(self):
        text = "Здравствуйте, Алексей. Меня зовут Дмитрий, я представитель ВТБ. Звоню по поводу назначенной на сегодня встречи для получения карты."
        answer = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "actions": [{"code": "introduce"}]})}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 15}}
        provider = FakeConnection([token(), {"data": [{"id": "GigaChat-2-Pro"}]}, answer])
        report = check_connection(provider, load_bundle())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["generation_requests"], 1)
        self.assertEqual(report["usage"]["completion_tokens"], 15)
        self.assertEqual(provider.methods, ["POST", "GET", "POST"])
        self.assertNotIn("test-only-key", json.dumps(report))
        self.assertNotIn("test-token", json.dumps(report))

    def test_unavailable_model_does_not_send_generation_request(self):
        provider = FakeConnection([token(), {"data": [{"id": "GigaChat-2"}]}])
        with self.assertRaises(ProviderError):
            check_connection(provider, load_bundle())
        self.assertEqual(provider.methods, ["POST", "GET"])

    def test_malformed_or_incomplete_reply_is_not_applied(self):
        replies = [{"choices": [None]}, {"choices": [{"finish_reason": "length"}]},
                   {"choices": [{"finish_reason": "blacklist"}]},
                   {"choices": [{"finish_reason": "stop", "message": {"content": "null"}}]}]
        for reply in replies:
            with self.subTest(reply=reply):
                provider = FakeConnection([token(), reply])
                session = new_session(load_bundle())
                with self.assertRaises((ProviderError, ContractError)):
                    provider.analyze(session, "Здравствуйте")
                self.assertEqual(session["turn_count"], 0)

    def test_http_error_does_not_expose_credentials(self):
        provider = GigaChatAnalyzer({"GIGACHAT_AUTH_KEY": "test-secret-marker"})
        exception = error.HTTPError(provider.TOKEN_URL, 401, "test-secret-marker", {}, None)
        with patch("do_vstrechi.gigachat.request.urlopen", side_effect=exception):
            with self.assertRaises(ProviderError) as captured:
                provider._token()
        self.assertNotIn("test-secret-marker", str(captured.exception))
        self.assertIn("401", str(captured.exception))

    def test_certificate_failure_keeps_tls_verification_enabled(self):
        provider = GigaChatAnalyzer({"GIGACHAT_AUTH_KEY": "test-only-key"})
        exception = error.URLError(ssl.SSLCertVerificationError("untrusted"))
        with patch("do_vstrechi.gigachat.request.urlopen", side_effect=exception):
            with self.assertRaises(ProviderError) as captured:
                provider._token()
        self.assertIn("GIGACHAT_CA_BUNDLE", str(captured.exception))
        self.assertEqual(provider.ssl_context.verify_mode, ssl.CERT_REQUIRED)


if __name__ == "__main__":
    unittest.main()
