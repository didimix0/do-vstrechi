import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from .contracts import ACTION_DESCRIPTIONS, ContractError
from .configure import configure_local_environment
from .connection import check_connection
from .data import ROOT, load_bundle
from .engine import advance, new_session, review_packet
from .conversation import naturalize_client_reply
from .gigachat import GigaChatAnalyzer, ProviderError, load_local_environment
from .storage import SessionStore
from .startup import DEFAULT_PORT

DEMO_TURNS = [
    ("introduce", "Здравствуйте, Алексей. Меня зовут Дмитрий, я представитель ВТБ. У нас сегодня назначена встреча для получения дебетовой карты. Всё в силе?"),
    ("offer_brief_explanation", "Понимаю. Давайте я кратко расскажу об условиях и пользе карты, а вы решите, нужна ли встреча."),
    ("ask_reason", "Что именно вас останавливает в получении ещё одной карты?"),
    ("explain_service", "Наша карта бесплатная."),
    ("explain_service_scope", "Выпуск и обслуживание самой карты бесплатные. Уведомления об операциях — отдельная услуга со своим тарифом, если их подключить."),
    ("connect_benefit", "Вы можете сохранить привычную карту, а эту рассмотреть как дополнительную: за обслуживание самой карты платить не нужно. На встрече разберём, пригодятся ли вам её условия."),
    ('explain_transfers', 'В ВТБ Онлайн переводы по номеру телефона через СБП в другие банки без комиссии до 300 тысяч рублей в месяц.'),
    ('explain_withdrawals', 'В банкоматах ВТБ и банков Группы ВТБ снятие до одного миллиона рублей в месяц без комиссии.'),
    ("propose_next_step", "Да, на встрече посмотрим условия вместе. Сохраняем встречу на ранее согласованное время?"),
    ("confirm_time", "Встречаемся сегодня в 18:00, верно?"),
    ("confirm_address", "По адресу: г. Учебный, ул. Примерная, д. 10. Всё верно?"),
    ("ask_passport_availability", "Подскажите, оригинал паспорта РФ будет у вас с собой на встрече?"),
]
DEMO_CLOSING = ("respect_refusal", "Понял вас. Передам отказ от встречи. Спасибо, до свидания.")


def print_client_response(session: dict) -> None:
    last = session["history"][-1]
    print("Событие:" if last["role"] == "event" else "Клиент:", last["text"])


def menu() -> int:
    while True:
        print("\nДо встречи — локальный запуск")
        print("1. Настроить ключ GigaChat (отдельное окно в Windows)")
        print("2. Проверить API (один реальный запрос генерации)")
        print("3. Тренировка: свободный текст + GigaChat")
        print("4. Пример без нейросети")
        print("5. Открыть браузерный тренажёр")
        print("0. Выход")
        choice = input("Номер: ").strip()
        if choice == "0":
            return 0
        modes = {"1": ["--configure"], "2": ["--check-api"],
                 "3": ["--provider", "gigachat"], "4": ["--demo"], "5": ["--serve", "--open-browser"]}
        if choice not in modes:
            print("Выберите номер от 0 до 5.")
            continue
        try:
            main(modes[choice])
        except (ProviderError, ContractError, ValueError) as exc:
            print("Действие не завершено:", str(exc))


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Первый текстовый слой проекта «До встречи»")
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--menu", action="store_true", help="Открыть меню локального запуска")
    commands.add_argument("--configure", action="store_true", help="Сохранить Authorization Key локально; в Windows откроется окно ввода")
    commands.add_argument("--check-api", action="store_true", help="Проверить доступ и выполнить один реальный анализ реплики")
    commands.add_argument("--check", action="store_true", help="Проверить согласованность данных")
    commands.add_argument("--demo", action="store_true", help="Подготовленный пример без нейросети")
    commands.add_argument("--serve", action="store_true", help="Запустить локальный браузерный интерфейс")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--provider", choices=["manual", "gigachat"], default="manual")
    parser.add_argument("--ending", choices=["appointment", "decline", "rude_decline", "hangup"],
                        default="appointment", help="Ветка клиента для проверки сценария")
    parser.add_argument("--database", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.menu:
        return menu()
    if args.configure:
        configure_local_environment()
        return 0
    if args.serve:
        if not 1 <= args.port <= 65535:
            parser.error("Порт должен быть от 1 до 65535.")
        from .webapp import serve
        return serve(args.port, args.open_browser, args.database)
    args.database = args.database or ROOT / "var/sessions.sqlite3"
    bundle = load_bundle()
    if args.check:
        print(f"Данные согласованы: звонок перед встречей, 1 паспорт продукта, {len(bundle['rubric']['criteria'])} критериев.")
        print("Проверена согласованность данных. Полнота источников и методика оценки проверяются отдельно.")
        return 0
    if args.demo and args.provider != "manual":
        parser.error("Демонстрационный прогон работает только без внешней модели.")
    load_local_environment()
    if args.check_api:
        print("Проверка API: авторизация, список моделей и одна вымышленная реплика.")
        output = ROOT / "var/connection_check.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            report = check_connection(GigaChatAnalyzer(), bundle)
        except (ProviderError, ContractError) as exc:
            failed = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
                      "status": "error", "message": str(exc)}
            output.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")
            raise
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Авторизация и доступ к модели:", report["model"], "— успешно")
        print("Структура ответа: проверена. Найденные действия:", ", ".join(report["probe_actions"]))
        print("Статус:", report["status"])
        print(report["notice"])
        print("Отчёт:", output)
        return 0 if report["status"] == "ok" else 1
    provider = GigaChatAnalyzer() if args.provider == "gigachat" else None
    session = new_session(bundle, args.ending)
    session["client_reply_mode"] = "natural" if provider else "scripted"
    store = SessionStore(args.database)
    store.create(session)
    mode = "scripted_demo" if args.demo else args.provider
    print("До встречи. Первый слой: текстовый сценарий.")
    print("Отдельная оценка доступна в браузерном тренажёре после звонка. Голос пока не подключён.")
    print("Режим:", "GigaChat понимает сотрудника и формулирует ответы клиента" if provider else "готовые действия и ответы, без нейросети")
    print("Ситуация:", bundle["scenario"]["brief"])
    appointment = bundle["scenario"]["appointment"]
    print("Учебная карточка встречи:", appointment["date_label"], appointment["time"], appointment["address"])
    if session["history"]:
        print_client_response(session)
    else:
        print("Начните разговор: первая реплика за вами.")
    demo = iter(DEMO_TURNS)
    while session["status"] == "active":
        if args.demo:
            code, employee_text = DEMO_CLOSING if session["phase"] == "final_refusal" else next(demo)
            print("Сотрудник:", employee_text)
            analysis = {"actions": [{"code": code, "quote": employee_text}]}
        else:
            employee_text = input("Сотрудник (или /end): ").strip()
            if not employee_text:
                continue
            if len(employee_text) > 4000:
                print("Сократите реплику до 4000 символов.")
                continue
            if employee_text == "/end":
                analysis = {"actions": [{"code": "end_meeting", "quote": employee_text}]}
            elif provider:
                try:
                    analysis = provider.analyze(session, employee_text)
                except (ProviderError, ContractError) as exc:
                    failed = deepcopy(session)
                    failed["calls"].append(dict(provider.metrics, error_type=type(exc).__name__))
                    session = store.save(failed, session["revision"])
                    print("Реплика не применена:", str(exc))
                    continue
            else:
                codes = list(ACTION_DESCRIPTIONS)
                print("Выберите действие для проверки сценария:")
                for i, code in enumerate(codes, 1):
                    print(f"{i}. {code}: {ACTION_DESCRIPTIONS[code]}")
                selected = input("Номер: ").strip()
                if not selected.isdigit() or not 1 <= int(selected) <= len(codes):
                    print("Неизвестное действие. Реплика не применена.")
                    continue
                analysis = {"actions": [{"code": codes[int(selected) - 1], "quote": employee_text}]}
        metrics = provider.metrics if provider and employee_text != "/end" else {}
        updated = advance(session, employee_text, analysis, mode=mode,
                          usage=metrics.get("usage"), elapsed_seconds=metrics.get("elapsed_seconds", 0))
        if provider and employee_text != "/end":
            updated = naturalize_client_reply(updated, provider)
        session = store.save(updated, session["revision"])
        if session.get("client_reply_notice"):
            print("Примечание:", session["client_reply_notice"])
        print_client_response(session)
    packet = review_packet(session)
    output = args.database.parent / (session["id"] + ".review.json")
    output.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    outcomes = {"appointment_agreed": "клиент согласился сохранить встречу",
                "ready_to_meet": "клиент готов встретиться, но детали ещё не подтверждены",
                "declined": "клиент отказался от встречи", "call_ended_by_client": "клиент завершил звонок",
                "undecided": "решение о встрече не получено"}
    print("Исход звонка:", outcomes[session["client_outcome"]])
    print("Учебный результат: не оценён. Сформирован пакет для проверки.")
    print("Материалы сессии:", output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ProviderError, ContractError, ValueError, EOFError, KeyboardInterrupt) as exc:
        print("Запуск остановлен:", str(exc))
        raise SystemExit(1)

