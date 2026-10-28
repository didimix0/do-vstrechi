"""Адаптер анализа действий. Сетевой вызов требует локального ключа."""
import json
import hashlib
import os
from pathlib import Path
import re
import ssl
import time
from urllib import error, parse, request
import uuid
from .contracts import ACTION_DESCRIPTIONS, ContractError, action_selection_schema, bind_action_evidence
from .data import ROOT
from .client import client_context, client_reply_schema, validate_client_reply
from .knowledge import select_product_context

ENV_KEYS = {"GIGACHAT_AUTH_KEY", "GIGACHAT_SCOPE", "GIGACHAT_MODEL", "GIGACHAT_CA_BUNDLE"}


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Повторяющийся ключ в JSON модели.")
        result[key] = value
    return result


class ProviderError(RuntimeError):
    pass


def load_local_environment(path: Path = ROOT / ".env") -> None:
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            if key.strip() in ENV_KEYS:
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


class GigaChatAnalyzer:
    TOKEN_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    CHAT_URL = "https://api.giga.chat/v1/chat/completions"

    def __init__(self, settings=None, prompt_path: Path | None = None):
        settings = os.environ if settings is None else settings
        self.auth_key = settings.get("GIGACHAT_AUTH_KEY", "").strip()
        if not self.auth_key:
            raise ProviderError("Ключ не настроен. Откройте start.cmd и выберите «Настроить ключ» либо запустите run.py --configure.")
        if any(c.isspace() for c in self.auth_key):
            raise ProviderError("Ключ содержит пробелы или переносы строк. Повторите локальную настройку.")
        self.scope = settings.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        self.model = settings.get("GIGACHAT_MODEL", "GigaChat-2-Pro")
        if self.scope not in {"GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"}:
            raise ProviderError("Некорректный GIGACHAT_SCOPE. Используйте значение из своего проекта API.")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", self.model):
            raise ProviderError("Некорректное имя модели в GIGACHAT_MODEL.")
        ca_file = settings.get("GIGACHAT_CA_BUNDLE") or None
        self.ssl_context = ssl.create_default_context()
        if ca_file:
            try:
                self.ssl_context.load_verify_locations(cafile=ca_file)
            except (OSError, ssl.SSLError):
                raise ProviderError("Не удалось загрузить доверенный PEM-сертификат. Проверьте GIGACHAT_CA_BUNDLE.") from None
        self.prompt = (prompt_path or ROOT / "prompts/analyze_turn.txt").read_text(encoding="utf-8")
        self.client_prompt = (ROOT / "prompts/client_role.txt").read_text(encoding="utf-8")
        self.access_token = None
        self.expires_at = 0.0
        self.metrics = {}
        self.request_timeout = 30

    def _request(self, url: str, body: bytes | None, headers: dict, method: str) -> dict:
        req = request.Request(url, data=body, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.request_timeout, context=self.ssl_context) as response:
                raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ProviderError("Ответ сервиса превышает допустимый размер.")
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ProviderError("Сервис вернул неверный формат ответа.")
            return value
        except error.HTTPError as exc:
            hints = {400: "Проверьте Scope, модель и параметры запроса.",
                     401: "Проверьте Authorization Key и Scope в локальных настройках.",
                     402: "Проверьте доступные токены и условия тарифа в кабинете.",
                     403: "У проекта нет доступа к запрошенной операции.",
                     429: "Достигнут лимит запросов или токенов. Повторите позже."}
            hint = hints.get(exc.code, "Сервис временно недоступен. Повторите позже.")
            raise ProviderError(f"Сервис вернул HTTP {exc.code}. {hint}") from None
        except (error.URLError, TimeoutError, OSError) as exc:
            reason = exc.reason if isinstance(exc, error.URLError) else exc
            self.metrics["transport_error_type"] = type(reason).__name__
            if isinstance(reason, TimeoutError):
                raise ProviderError("Сервис не ответил за отведённое время. Результат не применён; повторите позже.") from None
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise ProviderError("Не подтверждён сертификат сервиса. Укажите доверенный PEM в GIGACHAT_CA_BUNDLE; инструкция в docs/07-connect-gigachat.md.") from None
            raise ProviderError("Не удалось подключиться к сервису. Проверьте сеть и доверенный сертификат.") from None
        except (UnicodeError, json.JSONDecodeError):
            raise ProviderError("Сервис вернул некорректный JSON.") from None

    def _post(self, url: str, body: bytes, headers: dict) -> dict:
        return self._request(url, body, headers, "POST")

    def _get(self, url: str, headers: dict) -> dict:
        return self._request(url, None, headers, "GET")

    def available_models(self) -> list[str]:
        response = self._get("https://api.giga.chat/v1/models", {
            "Authorization": "Bearer " + self._token(), "Accept": "application/json"})
        models = response.get("data")
        if not isinstance(models, list):
            raise ProviderError("Сервис не вернул список моделей.")
        return sorted({item["id"] for item in models if isinstance(item, dict)
                       and isinstance(item.get("id"), str)
                       and re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", item["id"])})

    def _token(self) -> str:
        if self.access_token and time.time() < self.expires_at - 60:
            return self.access_token
        payload = self._post(self.TOKEN_URL, parse.urlencode({"scope": self.scope}).encode(), {
            "Authorization": "Basic " + self.auth_key, "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
        })
        if not isinstance(payload.get("access_token"), str) or not payload["access_token"]:
            raise ProviderError("В ответе авторизации отсутствует токен.")
        expires_at = payload.get("expires_at")
        if type(expires_at) not in (float, int) or expires_at / 1000 <= time.time():
            raise ProviderError("В ответе авторизации отсутствует действительный срок токена.")
        self.access_token = payload["access_token"]
        self.expires_at = expires_at / 1000
        return self.access_token

    def analyze(self, session: dict, employee_text: str) -> dict:
        context = {"employee_text": employee_text, "allowed_actions": ACTION_DESCRIPTIONS, "phase": session["phase"],
                   "product": select_product_context(session["materials"]["product"], employee_text, session["history"]),
                   "known_appointment": session["materials"]["scenario"]["appointment"],
                   "dialogue_features": session["materials"]["scenario"].get("dialogue_features", {}),
                   "explanation_memory": session.get('product_question_state', {}),
                   "history": session["history"]}
        active_id=session.get('product_question_state',{}).get('active_id')
        if active_id:
            context['current_question']=next(({k:q[k] for k in ('id','text','fact_ids','difficulty','defer_allowed') if k in q}
                                             for q in session['materials']['scenario']['product_questions'] if q['id']==active_id),None)
        selection = self._structured(self.prompt, context, action_selection_schema(), "action_analysis", 0, 1000)
        analysis = bind_action_evidence(selection, employee_text,
                                        current_question_id=session.get("product_question_state", {}).get("active_id"))
        self.metrics["passport_question_guard_applied"] = (
            any(a["code"] == "ask_passport_availability" for a in selection["actions"])
            and not any(a["code"] == "ask_passport_availability" for a in analysis["actions"]))
        self.metrics["status"] = "ok"
        return analysis

    def generate_client_reply(self, session: dict) -> str:
        context = client_context(session)
        try:
            reply = self._structured(self.client_prompt, context, client_reply_schema(context["reaction"]),
                                     "client_generation", 0.35, 450)
        finally:
            examples = context.get("approved_dialogue_examples", {})
            self.metrics.update(dialogue_examples_version=examples.get("version"),
                                dialogue_example_ids=[e["id"] for e in examples.get("examples", [])])
        try:
            text = validate_client_reply(reply, context)
        except ContractError:
            # Локальная диагностика позволяет отличить форматный сбой от решения контроллера.
            if isinstance(reply, dict) and isinstance(reply.get('text'), str):
                self.metrics['rejected_client_text'] = reply['text'][:600]
            raise
        self.metrics["status"] = "ok"
        return text

    def evaluate_session(self, session: dict) -> dict:
        from .evaluation import evaluation_context, evaluation_schema
        prompt = (ROOT / "prompts/evaluate_session.txt").read_text(encoding="utf-8")
        previous_timeout = self.request_timeout
        self.request_timeout = 90
        try:
            full_schema = evaluation_schema(session)
            # Форму JSON ограничивает API, размеры и допустимые ID проверяет приложение.
            # Короткая схема не разворачивает большие комбинации ограничений строк.
            def compact(value):
                if isinstance(value, dict):
                    return {k: compact(v) for k, v in value.items()
                            if k not in {"minLength", "maxLength", "minItems", "maxItems"}
                            and not (k == "enum" and len(v) > 10)}
                if isinstance(value, list):
                    return [compact(v) for v in value]
                return value
            context = {**evaluation_context(session), "output_schema": full_schema}
            return self._structured(prompt, context, compact(full_schema), "session_evaluation", 0, 6000)
        finally:
            self.request_timeout = previous_timeout

    def _structured(self, prompt: str, context: dict, schema: dict, role: str,
                    temperature: float, max_tokens: int) -> dict:
        start = time.perf_counter()
        self.metrics = {"mode": "gigachat", "role": role, "status": "error", "model": self.model,
                        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "usage": {}}
        try:
            payload = {"model": self.model, "temperature": temperature, "max_tokens": max_tokens,
                       "messages": [{"role": "system", "content": prompt},
                                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                       "response_format": {"type": "json_schema", "schema": schema, "strict": True}}
            response = self._post(self.CHAT_URL, json.dumps(payload, ensure_ascii=False).encode("utf-8"), {
                "Authorization": "Bearer " + self._token(), "Content-Type": "application/json",
                "Accept": "application/json",
            })
            raw_usage = response.get("usage", {})
            if isinstance(raw_usage, dict):
                self.metrics["usage"] = {k: v for k, v in raw_usage.items() if type(v) is int and v >= 0}
            choices = response.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ProviderError("Модель не вернула ожидаемый структурированный ответ.")
            choice = choices[0]
            if choice.get("finish_reason") != "stop":
                raise ProviderError("Генерация не завершена либо запрос отклонён. Реплика не применена.")
            message = choice.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise ProviderError("Модель не вернула ожидаемый структурированный ответ.")
            try:
                output = json.loads(message["content"], object_pairs_hook=unique_json_object)
            except ValueError:
                raise ProviderError("Модель не вернула ожидаемый структурированный ответ.") from None
            return output
        finally:
            self.metrics["elapsed_seconds"] = round(time.perf_counter() - start, 3)

