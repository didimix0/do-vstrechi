"""Локальный HTTP-интерфейс существующего ядра. Только loopback, без зависимостей."""
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import urlsplit
import webbrowser
from .contracts import ACTION_DESCRIPTIONS, ContractError
from .data import ROOT, fingerprint, load_bundle, passport_question_enabled
from .engine import advance, now, new_session, review_packet
from .conversation import naturalize_client_reply
from .gigachat import GigaChatAnalyzer, ProviderError, load_local_environment
from .storage import SessionStore
from .questions import product_questions_enabled
from .evaluation import apply_evaluation, input_hash, session_rating
from .characters import profiles, selected_profile
from .voice_input import VoiceRecognizer, MAX_BYTES
from .startup import DEFAULT_PORT,APP_ID,runtime_revision,matching_server,make_legacy_redirect,LocalHTTPServer

OPTIONS = {
    "intro": ("Здравствуйте, Алексей. Меня зовут Дмитрий, я представитель ВТБ. Звоню по поводу назначенной на сегодня встречи для получения карты. Всё в силе?", ["introduce"]),
    "offer": ("Понимаю. Можно я кратко расскажу об условиях карты, а вы решите, есть ли смысл встречаться?", ["offer_brief_explanation"]),
    "reason": ("Подскажите, что именно вас останавливает в получении ещё одной карты?", ["ask_reason"]),
    "service": ("Выпуск и обслуживание самой карты бесплатные. Уведомления об операциях — отдельная услуга: 129 рублей в месяц при подключении; для отдельных уровней программы банка действуют другие условия.", ["explain_service", "explain_service_scope"]),
    "cashback": ("Есть кешбэк рублями за подходящие покупки в выбранных категориях. Категории выбираются ежемесячно, действуют ограничения.", ["explain_cashback", "explain_cashback_mechanics"]),
    "basic_service": ("Наша карта бесплатная.", ["explain_service"]),
    "benefit": ("Вы можете сохранить привычную карту, а эту рассмотреть как дополнительную: за обслуживание самой карты платить не нужно. На встрече разберём, пригодятся ли вам её условия.", ["connect_benefit"]),
    "cashback_limit": ("Общий предел кешбэка за покупки по карте — три тысячи рублей в месяц. Это максимум начисления, а не гарантированная выплата. Отдельные предложения могут иметь свои ограничения.", ["explain_cashback_limits"]),
    "transfers": ("В ВТБ Онлайн переводы по номеру телефона через СБП в другие банки — без комиссии до 300 тысяч рублей в месяц.", ['explain_transfers']),
    "withdrawals": ("В банкоматах ВТБ и банков Группы ВТБ снятие до одного миллиона рублей в месяц без комиссии. Для других банкоматов условия отличаются.", ['explain_withdrawals']),
    'cashback_rate':('В предложении указан кешбэк до 15 процентов в категориях. Это максимальная ставка для отдельных категорий, а не процент на все покупки.',['explain_current_detail']),
    'cashback_categories':('Если клиенту доступен пробный период для новых клиентов, можно выбрать пять категорий. После него число зависит от уровня программы банка; конкретные условия нужно смотреть в ВТБ Онлайн.',['explain_current_detail']),
    'trial_categories':('В пробный период входит месяц открытия карты и ещё два следующих месяца. Это не три полных месяца после даты открытия. Право на этот период нужно проверить.',['explain_current_detail']),
    'withdrawal_limits':('Это разные ограничения. Общий лимит снятия составляет 350 тысяч рублей в день и 2 миллиона в месяц суммарно по картам пакета. Бесплатный объём в банкоматах ВТБ и банков Группы ВТБ для обычного клиента — один миллион рублей в месяц.',['explain_current_detail']),
    'withdrawal_fee_base':('В банкоматах ВТБ и банков Группы ВТБ при превышении бесплатного объёма одного миллиона рублей в месяц комиссия составляет 1,9 процента именно от суммы превышения, не со всей суммы.',['explain_current_detail']),
    'clarify_later':('Этот момент я уточню по правилам банка и подробно объясню на встрече. Сейчас не хочу называть условия по памяти.',['defer_to_meeting']),
    "meeting_details": ("Подробные условия по этой теме предлагаю вместе посмотреть на встрече. Сейчас не буду обещать то, что нужно уточнить.", ['defer_to_meeting']),
    "defer": ("Точно ответить на этот вопрос сейчас не могу. Нужно проверить соответствующие условия; обещать неподтверждённое не буду.", ["defer_product_detail"]),
    "premature": ("Давайте тогда просто встретимся и оформим карту.", ["propose_next_step"]),
    "promise": ("По этой карте кешбэк начисляется на любые покупки без ограничений.", ["possible_product_error"]),
    "correct": ("Уточню сказанное: кешбэк начисляется за подходящие покупки в выбранных категориях, есть ограничения и лимит.", ["clarify_product_terms", "explain_cashback"]),
    "meeting": ("Да, на встрече можно подробнее посмотреть условия. Сохраняем назначенную встречу?", ["propose_next_step"]),
    "time": ("Встречаемся сегодня в {time}, верно?", ["confirm_time"]),
    "address": ("По адресу: {address}. Всё верно?", ["confirm_address"]),
    "both": ("Встречаемся сегодня в {time} по адресу: {address}. Всё верно?", ["confirm_time", "confirm_address"]),
    "passport": ("Подскажите, оригинал паспорта РФ будет у вас с собой на встрече?", ["ask_passport_availability"]),
    "goodbye": ("Спасибо. Тогда до встречи!", ["end_meeting"]),
    "close": ("Понял вас. Передам отказ от встречи. Спасибо, до свидания.", ["respect_refusal"]),
    "pressure": ("Вы всё равно должны получить эту карту, давайте оформим её.", ["pressure"]),
}
OUTCOMES = {"undecided": "Решение ещё не получено", "ready_to_meet": "Готов встретиться · уточняем детали",
            "appointment_agreed": "Встреча подтверждена", "declined": "Клиент отказался",
            "call_ended_by_client": "Клиент завершил звонок"}
PHASES = {"call_opening": "Начало звонка", "objection": "Первое возражение", "listening": "Клиент готов выслушать",
          "clarification": "Причина сомнений", "explanation": "Объяснение условий",
          "confirmation": "Согласование встречи", "final_refusal": "Завершение после отказа", "ended": "Звонок завершён"}


class ApiError(Exception):
    def __init__(self, status, message, state=None):
        self.status, self.message, self.state = status, message, state
        super().__init__(message)


class TrainerApp:
    def __init__(self, database: Path, provider_factory=GigaChatAnalyzer):
        self.bundle = load_bundle()
        self.store = SessionStore(database)
        self.provider_factory = provider_factory
        self.provider = None
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.voice = VoiceRecognizer()
        self.runtime_revision=runtime_revision()

    def bootstrap(self):
        try:
            self.provider_factory()
            ai_ready, reason = True, ""
        except ProviderError as exc:
            ai_ready, reason = False, str(exc)
        scenario = self.bundle["scenario"]
        return {"token": self.token, "ai_ready": ai_ready, "ai_hint": reason,
                "voice": self.voice.status(),
                "characters": [{k:p[k] for k in ('id','label','description')} for p in profiles(scenario)],
                "exercise": {"title": scenario["title"], "brief": scenario["brief"], "version": scenario["version"],
                             "appointment": scenario["appointment"], "client_name": scenario["persona"]["name"]}}

    def get(self, session_id):
        try:
            return self.store.get(session_id)
        except KeyError:
            raise ApiError(404, "Тренировка не найдена.") from None

    def options(self, session):
        flags = session["flags"]
        if session["status"] != "active" or session["mode"] != "guided":
            return []
        if flags["firm_refusal"]:
            keys = ["close", "pressure"]
        elif flags["pending_product_clarification"]:
            keys = ["correct", "meeting", "service"]
        elif not flags["introduced"]:
            keys = ["intro"]
        elif product_questions_enabled(session["materials"]["scenario"]) and session.get("product_question_state", {}).get("active_id"):
            active = session["product_question_state"]["active_id"]
            answer = {"service_scope": "service", "benefit_relevance": "benefit", "cashback_mechanics": "cashback", "cashback_limit": "cashback_limit", 'transfers':'transfers','cash_withdrawal':'withdrawals'}.get(active,active)
            keys = [answer, "meeting", "defer", "promise"]
            definition=next(q for q in session['materials']['scenario']['product_questions'] if q['id']==active)
            if definition.get('difficulty')=='advanced' and session.get('product_question_state',{}).get('accepted_deferrals',0)<1:
                keys.insert(1,'clarify_later')
            if active in {'transfers','cash_withdrawal'} and not session['materials']['scenario'].get('dialogue_features',{}).get('distinct_client_behavior'):
                keys.insert(1, 'meeting_details')
        elif flags["meeting_ready"]:
            keys = ["both", "time", "address"]
            if passport_question_enabled(session["materials"]["scenario"]):
                if not flags.get("passport_answered"):
                    keys.insert(0, "passport")
                keys.append("goodbye")
        elif not (flags["service_discussed"] or flags["cashback_discussed"]):
            keys = ["offer", "reason", "service", "premature"] if not flags["brief_accepted"] else ["service", "cashback", "reason", "promise"]
            if product_questions_enabled(session["materials"]["scenario"]):
                keys.append("basic_service")
        else:
            keys = ["service", "meeting", "cashback", "promise"] if flags["pending_service_question"] else ["meeting", "cashback", "promise"]
            if passport_question_enabled(session["materials"]["scenario"]) and not flags["pending_service_question"]:
                keys.append("passport")
        if not product_questions_enabled(session["materials"]["scenario"]):
            # Новые действия готовых фраз не применяются к материалам прежних попыток.
            keys = [key for key in keys if key not in {"basic_service", "benefit", "cashback_limit", "defer"}]
        return [{"id": key, "text": self.option_value(session, key)[0]} for key in keys]

    def option_value(self, session, key):
        text, codes = OPTIONS[key]
        if not product_questions_enabled(session["materials"]["scenario"]):
            codes = [code for code in codes if code not in {"explain_service_scope", "explain_cashback_mechanics"}]
        return text.format(**session["materials"]["scenario"]["appointment"]), codes

    def public(self, session):
        return {key: deepcopy(session[key]) for key in ("id", "revision", "status", "created_at", "mode", "turn_count", "history")} | {
            "version_notice": ('Эта попытка создана по предыдущим правилам. Начните новую, чтобы использовать обновлённый сценарий.'
                               if session['material_hashes']['scenario'] != fingerprint(self.bundle['scenario']) else ''),
            "phase_label": PHASES[session["phase"]], "outcome_label": OUTCOMES[session["client_outcome"]],
            "rating": session_rating(session),
            "character": {k:v for k,v in session.get('client_profile',{}).items() if k in {'id','label','description'}},
            "options": self.options(session), "max_turns": session["materials"]["scenario"]["max_turns"],
            "client_reply_mode": session.get("client_reply_mode", "scripted"),
            "client_reply_notice": session.get("client_reply_notice", "") or (
                "В этой сохранённой попытке используются готовые ответы клиента. Для нового режима начните новую попытку."
                if session["mode"] == "gigachat" and session.get("client_reply_mode") != "natural" else ""),
            "appointment": session["materials"]["scenario"]["appointment"],
            "client_name": session["materials"]["scenario"]["persona"]["name"]}

    def create(self, body):
        if not {'mode','ending'} <= set(body) or set(body)-{'mode','ending','character'} or not isinstance(body["mode"], str) or body["mode"] not in {"guided", "gigachat"}:
            raise ApiError(400, "Выберите режим тренировки.")
        if not isinstance(body["ending"], str) or body["ending"] not in {"appointment", "decline", "rude_decline", "hangup"}:
            raise ApiError(400, "Неизвестная ветка учебного примера.")
        with self.lock:
            # Новая попытка получает текущие данные и инструкции, прежняя хранит снимок.
            self.bundle = load_bundle()
            if body["mode"] == "gigachat":
                self.provider = self.provider_factory()
            character=body.get('character','calm')
            if not isinstance(character,str) or selected_profile(self.bundle['scenario'],character) is None:
                raise ApiError(400,'Выберите характер клиента из списка.')
            session = new_session(self.bundle, body["ending"], character)
            session.update(mode=body["mode"], requests={},
                           client_reply_mode="natural" if body["mode"] == "gigachat" else "scripted")
            self.store.create(session)
        return self.public(session)

    def mutate(self, session_id, body, kind="turn"):
        if not isinstance(body.get("request_id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", body["request_id"]):
            raise ApiError(400, "Неверный идентификатор отправки.")
        if type(body.get("revision")) is not int:
            raise ApiError(400, "Нужна версия тренировки.")
        digest = hashlib.sha256(json.dumps({"kind": kind, "body": body}, sort_keys=True).encode()).hexdigest()
        with self.lock:
            session = self.get(session_id)
            previous = session["requests"].get(body["request_id"])
            if previous:
                if previous["digest"] != digest:
                    raise ApiError(409, "Этот идентификатор уже использован для другой отправки.", self.public(session))
                if previous.get("error"):
                    raise ApiError(502, previous["error"], self.public(session))
                return self.public(session)
            if session["revision"] != body["revision"]:
                raise ApiError(409, "Тренировка изменилась. Показана актуальная версия.", self.public(session))
            if session["status"] != "active":
                raise ApiError(409, "Звонок уже завершён.", self.public(session))
            if kind == "finish":
                if set(body) != {"request_id", "revision"}:
                    raise ApiError(400, "Неверный формат завершения.")
                updated = deepcopy(session)
                updated.update(status="ended", phase="ended", end_reason="employee_stopped")
                updated["history"].append({"id": "stopped", "role": "event", "type": "employee_stopped",
                                           "text": "Упражнение завершено сотрудником."})
            else:
                if session["mode"] == "guided":
                    if set(body) != {"request_id", "revision", "option_id"} or not isinstance(body.get("option_id"), str) or body["option_id"] not in {o["id"] for o in self.options(session)}:
                        raise ApiError(400, "Выберите одну из доступных реплик.")
                    text, codes = self.option_value(session, body["option_id"])
                    analysis = {"actions": [{"code": code, "quote": text} for code in codes]}
                    metrics = {}
                else:
                    if set(body) != {"request_id", "revision", "text"} or not isinstance(body.get("text"), str) or not 1 <= len(body["text"].strip()) <= 4000:
                        raise ApiError(400, "Введите реплику от 1 до 4000 символов.")
                    text = body["text"].strip()
                    if self.provider is None:
                        self.provider = self.provider_factory()
                    try:
                        analysis = self.provider.analyze(session, text)
                    except (ProviderError, ContractError) as exc:
                        failed = deepcopy(session)
                        failed["calls"].append(dict(self.provider.metrics, error_type=type(exc).__name__))
                        failed["requests"][body["request_id"]] = {"digest": digest, "error": str(exc)}
                        saved = self.store.save(failed, session["revision"])
                        raise ApiError(502, str(exc), self.public(saved)) from None
                    metrics = deepcopy(self.provider.metrics)
                updated = advance(session, text, analysis, mode=session["mode"], usage=metrics.get("usage"),
                                  elapsed_seconds=metrics.get("elapsed_seconds", 0))
                if session["mode"] == "gigachat":
                    updated = naturalize_client_reply(updated, self.provider)
            updated["requests"][body["request_id"]] = {"digest": digest}
            return self.public(self.store.save(updated, session["revision"]))

    def review(self, session_id):
        session = self.get(session_id)
        if session["status"] != "ended":
            raise ApiError(409, "Завершите упражнение перед разбором.")
        packet = review_packet(session)
        packet["history"] = session["history"]
        packet["outcome_label"] = OUTCOMES[session["client_outcome"]]
        packet["turn_count"] = session["turn_count"]
        return packet

    def evaluate(self, session_id, body):
        if set(body) != {"request_id", "revision"} or type(body["revision"]) is not int or not isinstance(body["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", body["request_id"]):
            raise ApiError(400, "Неверный формат запроса оценки.")
        digest = hashlib.sha256(json.dumps({"kind": "evaluate", "body": body}, sort_keys=True).encode()).hexdigest()
        with self.lock:
            session = self.get(session_id)
            previous = session["requests"].get(body["request_id"])
            if previous:
                if previous["digest"] != digest:
                    raise ApiError(409, "Этот идентификатор уже использован для другого действия.", self.public(session))
                if previous.get("error"):
                    raise ApiError(502, previous["error"], self.public(session))
                return {"session": self.public(session), "review": self.review(session_id)}
            if session["revision"] != body["revision"]:
                raise ApiError(409, "Попытка изменилась. Обновите разбор.", self.public(session))
            if session["status"] != "ended":
                raise ApiError(409, "Завершите звонок перед оценкой.", self.public(session))
            # Даже новый идентификатор запроса не запускает повторную платную проверку.
            if session.get("evaluation", {}).get("input_hash") == input_hash(session):
                return {"session": self.public(session), "review": self.review(session_id)}
            if self.provider is None:
                self.provider = self.provider_factory()
            try:
                updated = apply_evaluation(session, self.provider)
            except (ProviderError, ContractError) as exc:
                updated = deepcopy(session)
                message = "Автоматический разбор не завершён из-за ошибки сервиса или несогласованного ответа. Разговор сохранён. Нажмите «Повторить автооценку»."
                updated["evaluation_error"] = message
                updated["calls"].append(dict(self.provider.metrics, role="session_evaluation", status="error", error_type=type(exc).__name__, at=now()))
                if isinstance(exc, ContractError):
                    updated["calls"][-1]["validation_detail"] = str(exc)
                updated["requests"][body["request_id"]] = {"digest": digest, "error": message}
                saved = self.store.save(updated, session["revision"])
                raise ApiError(502, message, self.public(saved)) from None
            updated["requests"][body["request_id"]] = {"digest": digest}
            saved = self.store.save(updated, session["revision"])
            return {"session": self.public(saved), "review": self.review(session_id)}


def make_server(app: TrainerApp, port: int = DEFAULT_PORT, *, access=None):
    assets = {"/": ("index.html", "text/html"), "/app.css": ("app.css", "text/css"),
              "/app.js": ("app.js", "text/javascript"), "/favicon.svg": ("favicon.svg", "image/svg+xml"),
              "/voice.js": ("voice.js", "text/javascript"), "/audio-input.js": ("audio-input.js", "text/javascript")}
    assets['/review-ui.js']=('review-ui.js','text/javascript')
    assets['/vtb-theme.css']=('vtb-theme.css','text/css')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, data, mime="application/json", headers=None):
            raw = json.dumps(data, ensure_ascii=False).encode() if mime == "application/json" else data
            self.send_response(status)
            self.send_header("Content-Type", mime + ("; charset=utf-8" if mime != "image/svg+xml" else ""))
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header('X-Trainer-App',APP_ID)
            self.send_header('X-Trainer-Revision',app.runtime_revision)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(raw)

        def guard(self):
            if access is not None:
                self.app = access.guard(self)
                return
            self.app = app
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in allowed:
                raise ApiError(403, "Доступ разрешён только через локальный адрес приложения.")
            if self.command == "POST":
                origin = self.headers.get("Origin")
                if origin is not None and origin not in {"http://" + host for host in allowed}:
                    raise ApiError(403, "Недопустимый источник запроса.")
                if not secrets.compare_digest(self.headers.get("X-Trainer-Token", ""), app.token):
                    raise ApiError(403, "Обновите страницу приложения.")

        def do_GET(self):
            try:
                if access is not None and access.handle(self):
                    return
                self.guard()
                current_app = self.app
                path = urlsplit(self.path).path
                if path in assets:
                    name, mime = assets[path]
                    self.respond(200, (ROOT / "web" / name).read_bytes(), mime)
                elif path == "/api/bootstrap":
                    self.respond(200, current_app.bootstrap())
                elif path == "/api/sessions":
                    self.respond(200, [{k: v for k, v in current_app.public(s).items() if k in {"id", "created_at", "mode", "status", "turn_count", "outcome_label", "rating", "character"}}
                                       for s in current_app.store.recent()])
                elif match := re.fullmatch(r"/api/sessions/([a-f0-9-]{36})(/review)?", path):
                    self.respond(200, current_app.review(match[1]) if match[2] else current_app.public(current_app.get(match[1])))
                else:
                    raise ApiError(404, "Страница не найдена.")
            except ApiError as exc:
                self.respond(exc.status, {"error": exc.message, "session": exc.state})

        def do_POST(self):
            try:
                if access is not None and access.handle(self):
                    return
                self.guard()
                current_app = self.app
                path = urlsplit(self.path).path
                length = self.headers.get("Content-Length", "")
                if path == '/api/voice/transcribe':
                    if (not length.isdigit() or not 44 <= int(length) <= MAX_BYTES
                            or self.headers.get('Content-Type','').split(';')[0] != 'audio/wav'):
                        raise ApiError(400, 'Нужна запись WAV длительностью до 45 секунд.')
                    self.respond(200, current_app.voice.recognize(self.rfile.read(int(length))))
                    return
                if not length.isdigit() or not 1 <= int(length) <= 20_000 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ApiError(400, "Ожидается JSON-запрос допустимого размера.")
                raw = self.rfile.read(int(length))
                try:
                    body = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise ApiError(400, "Неверный JSON.") from None
                if not isinstance(body, dict):
                    raise ApiError(400, "Ожидается объект запроса.")
                if path == "/api/sessions":
                    self.respond(201, current_app.create(body))
                elif match := re.fullmatch(r"/api/sessions/([a-f0-9-]{36})/(turns|finish|evaluate)", path):
                    if match[2] == "evaluate":
                        self.respond(200, current_app.evaluate(match[1], body))
                    else:
                        self.respond(200, current_app.mutate(match[1], body, "finish" if match[2] == "finish" else "turn"))
                else:
                    raise ApiError(404, "Маршрут не найден.")
            except ApiError as exc:
                if exc.status in {401, 403, 429}:
                    # В Windows закрытие сокета с непрочитанным телом иногда
                    # сбрасывает соединение раньше доставки ответа 403.
                    size=self.headers.get('Content-Length','')
                    if size.isdigit() and 0<int(size)<=MAX_BYTES:
                        try:
                            self.connection.settimeout(1)
                            self.rfile.read(int(size))
                        except OSError:
                            pass
                        finally:
                            self.connection.settimeout(None)
                self.respond(exc.status, {"error": exc.message, "session": exc.state})
            except (ProviderError, ContractError, ValueError) as exc:
                self.respond(400, {"error": str(exc)})
            finally:
                if access is not None:
                    access.release(self)

    return LocalHTTPServer(("127.0.0.1", port), Handler)


def serve(port=DEFAULT_PORT, open_browser=False, database=None):
    load_local_environment()
    app = TrainerApp(database or ROOT / "var/web-sessions.sqlite3")
    address=f'http://127.0.0.1:{port}'
    if matching_server(address,app.runtime_revision):
        print('Тренажёр уже работает:',address,flush=True)
        if open_browser:webbrowser.open(address)
        return 0
    try:
        server = make_server(app, port)
    except OSError:
        address=f'http://127.0.0.1:{port}'
        if matching_server(address,app.runtime_revision):
            print('Тренажёр уже работает:',address,flush=True)
            if open_browser:webbrowser.open(address)
            return 0
        raise ValueError('На этом адресе работает другой или прежний запуск. Закройте окно прежнего тренажёра и запустите start.cmd заново.') from None
    address = f"http://127.0.0.1:{server.server_port}"
    redirect=None;redirect_thread=None
    if server.server_port==DEFAULT_PORT:
        try:
            redirect=make_legacy_redirect()
        except OSError:
            print('Используйте актуальный адрес:',address,flush=True)
        else:
            redirect_thread=threading.Thread(target=redirect.serve_forever,daemon=True);redirect_thread.start()
    print("До встречи. Локальный интерфейс:", address, flush=True)
    print("Для остановки нажмите Ctrl+C. Ключи остаются на сервере.", flush=True)
    if open_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Локальный сервер остановлен.")
    finally:
        server.server_close()
        if redirect:
            redirect.shutdown();redirect.server_close();redirect_thread.join(timeout=3)
    return 0
