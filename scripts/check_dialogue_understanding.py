"""Точечная проверка понимания и клиентских реакций. --live расходует токены."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from do_vstrechi.conversation import naturalize_client_reply
from do_vstrechi.data import load_bundle
from do_vstrechi.engine import advance, new_session
from do_vstrechi.gigachat import GigaChatAnalyzer, load_local_environment


class CapturingClient(GigaChatAnalyzer):
    last_client_payload = None

    def _structured(self, *args, **kwargs):
        result = super()._structured(*args, **kwargs)
        if (args[3] if len(args) > 3 else kwargs.get("role")) == "client_generation":
            self.last_client_payload = deepcopy(result)
        return result


def fixture(session, text, *codes):
    return advance(session, text, {"actions": [{"code": c, "quote": text} for c in codes]}, mode="fixture")


def run():
    if "--live" not in sys.argv:
        print("Для обращения к API нужен --live. Без API: python -m unittest discover -s tests -q.")
        return 0
    load_local_environment()
    provider = CapturingClient()
    bundle = load_bundle()
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "synthetic": True,
              "scenario_version": bundle["scenario"]["version"], "cases": [], "calls": [], "status": "running"}

    def intro():
        session = new_session(bundle)
        session["client_reply_mode"] = "natural"
        return fixture(session, "Здравствуйте, я представитель ВТБ, звоню о встрече для получения карты.", "introduce")

    def real_turn(session, text, expected, name):
        analysis = provider.analyze(session, text)
        report["calls"].append(deepcopy(provider.metrics))
        session = advance(session, text, analysis, mode="gigachat")
        provider.last_client_payload = None
        session = naturalize_client_reply(session, provider)
        report["calls"].append(deepcopy(session["calls"][-1]))
        last = session["history"][-1]
        case = {"name": name, "employee": text, "actions": [a["code"] for a in analysis["actions"]],
                "client": last["text"], "raw_client": provider.last_client_payload, "reaction": last["reaction"], "source": last["reply_source"],
                "ok": last["reaction"] == expected and last["reply_source"] == "generated"}
        report["cases"].append(case)
        print(json.dumps(case, ensure_ascii=False), flush=True)
        return session

    try:
        session = fixture(intro(), "Карта бесплатная.", "explain_service")
        session = real_turn(session, "А что именно вас останавливает в получении ещё одной карты?", "reason", "answer_employee_question")
        report["cases"][-1]["question_asks"] = len(session["product_question_state"]["records"][0]["asked_turn_ids"])
        report["cases"][-1]["ok"] &= report["cases"][-1]["question_asks"] == 1

        session = fixture(intro(), "Карта бесплатная.", "explain_service")
        session = real_turn(session, "Не знаю.", "product_question", "honest_unknown_then_new_topic")
        report["cases"][-1]["topic"] = session["history"][-1].get("product_question_id")
        report["cases"][-1]["ok"] &= report["cases"][-1]["topic"] == "benefit_relevance"

        session = new_session(bundle)
        session["client_reply_mode"] = "natural"
        text = "Здравствуйте, я Дмитрий, представитель ВТБ. Звоню перед встречей для получения карты. Выпуск и обслуживание бесплатны, уведомления при подключении стоят 129 рублей в месяц по базовому условию; для других уровней действуют свои условия."
        session = real_turn(session, text, "objection", "remember_long_introduction")
        report["cases"][-1]["ok"] &= "service_scope" in session["product_question_state"]["covered"]
        session = fixture(session, "Можно кратко рассказать о пользе дополнительной карты?", "offer_brief_explanation")
        report["cases"].append({"name": "no_repeat_of_explained_service", "question": session["history"][-1].get("product_question_id"),
                                "ok": session["history"][-1].get("product_question_id") == "benefit_relevance"})

        # Здесь отдельно проверяется анализ неполного ответа; генерация уже покрыта тестами.
        session = fixture(intro(), "Выпуск и обслуживание бесплатны, уведомления при подключении платные.", "explain_service_scope")
        session = fixture(session, "Привычную карту можно оставить, а эту рассмотреть как дополнительную без платы за обслуживание.", "connect_benefit")
        session = fixture(session, "Есть кешбэк.", "explain_cashback")
        session = fixture(session, "Каждый месяц выбираете категории, получаете возврат за подходящие покупки.", "explain_cashback_mechanics")
        text = "Лимиты есть."
        analysis = provider.analyze(session, text)
        report["calls"].append(deepcopy(provider.metrics))
        session = advance(session, text, analysis, mode="gigachat")
        report["cases"].append({"name": "vague_limit_stays_open", "actions": [a["code"] for a in analysis["actions"]],
                                "question": session["history"][-1].get("product_question_id"),
                                "ok": session["history"][-1].get("product_question_id") == "cashback_limit"})
        report["status"] = "ok" if all(case["ok"] for case in report["cases"]) else "needs_review"
    except Exception as exc:
        report.update(status="error", error_type=type(exc).__name__, error=str(exc))
    path = ROOT / "var/dialogue-understanding-live.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(path)}, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(run())
