"""Короткая проверка уточнений на вымышленных репликах; --live расходует токены."""
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


def fixture(session, text, *codes):
    return advance(session, text, {"actions": [{"code": code, "quote": text} for code in codes]}, mode="fixture")


def run():
    if "--live" not in sys.argv:
        print("Для проверки без API: python -m unittest discover -s tests -q. С API: добавьте --live.")
        return 0
    load_local_environment()
    provider = GigaChatAnalyzer()
    bundle = load_bundle()
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "synthetic": True,
              "model": provider.model, "scenario_version": bundle["scenario"]["version"],
              "cases": [], "calls": [], "status": "running"}

    def prepared():
        session = new_session(bundle)
        session["client_reply_mode"] = "natural"
        return fixture(session, "Здравствуйте, я Дмитрий, представитель ВТБ. Звоню перед встречей для выдачи карты.", "introduce")

    def generated(session):
        result = naturalize_client_reply(session, provider)
        call = deepcopy(result["calls"][-1])
        report["calls"].append(call)
        assert call["status"] == "ok", "Модель не сформулировала допустимый ответ клиента."
        return result

    try:
        # Реальный анализ неясного ответа и признания незнания после вопроса об источнике.
        session = fixture(prepared(), "Кешбэк на всё без ограничений.", "possible_product_error")
        reactions = []
        for text, expected in [("На сайте.", "source_question"), ("Не знаю, где именно.", "unanswered_goodbye")]:
            analysis = provider.analyze(session, text)
            report["calls"].append(deepcopy(provider.metrics))
            session = advance(session, text, analysis, mode="gigachat")
            session = generated(session)
            last = session["history"][-1]
            reactions.append(last["reaction"])
            no_false_explanation = text != "На сайте." or not any(a["code"].startswith("explain_") for a in analysis["actions"])
            report["cases"].append({"case": "source_without_answer", "employee": text,
                                    "actions": [a["code"] for a in analysis["actions"]],
                                    "client": last["text"], "reaction": last["reaction"],
                                    "ok": last["reaction"] == expected and no_false_explanation})
        assert session["status"] == "ended" and reactions == ["source_question", "unanswered_goodbye"]

        # Реальный классификатор распознаёт короткое признание незнания.
        session = fixture(prepared(), "Карта бесплатная.", "explain_service")
        analysis = provider.analyze(session, "Не знаю.")
        report["calls"].append(deepcopy(provider.metrics))
        codes = [a["code"] for a in analysis["actions"]]
        report["cases"].append({"case": "plain_unknown", "actions": codes, "ok": "defer_product_detail" in codes})

        # Для перехода темы действия заданы явно: отдельно проверяем формулирование моделью.
        session = fixture(session, "Посмотрите сами.", "unclear")
        session = fixture(session, "Ничего больше сказать не могу.", "unclear")
        session = generated(session)
        last = session["history"][-1]
        ok = last.get("product_question_id") == "benefit_relevance" and last.get("switched_from_question_id") == "service_scope"
        report["cases"].append({"case": "switch_after_two_questions", "client": last["text"],
                                "reaction": last["reaction"], "topic": last.get("product_question_id"), "ok": ok})
        report["status"] = "ok" if all(c["ok"] for c in report["cases"]) else "needs_review"
    except Exception as exc:
        report.update(status="error", error_type=type(exc).__name__, error=str(exc))
    output = ROOT / "var/question-patience-live-check.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "cases": report["cases"], "report": str(output)}, ensure_ascii=False))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(run())
