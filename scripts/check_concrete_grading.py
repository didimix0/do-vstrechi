"""Два синтетических разговора для оценки конкретики. --live вызывает GigaChat."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from check_evaluation import CapturingEvaluator, cases
from do_vstrechi.data import load_bundle
from do_vstrechi.engine import new_session
from do_vstrechi.evaluation import apply_evaluation
from do_vstrechi.gigachat import load_local_environment
from do_vstrechi.storage import SessionStore


def prepared_sessions():
    poor = new_session(load_bundle())
    poor.update(status="ended", phase="ended", end_reason="employee_ended", client_outcome="declined", turn_count=4)
    poor["history"] = [
        {"id": "e1", "role": "employee", "text": "Здравствуйте, я Дмитрий, представитель ВТБ. Звоню подтвердить встречу для получения заказанной карты."},
        {"id": "c1", "role": "client", "text": "Я передумал, привык к другому банку. Не понимаю, зачем мне ещё одна карта."},
        {"id": "e2", "role": "employee", "text": "Много кэшбэка."},
        {"id": "c2", "role": "client", "text": "Какой именно кешбэк, за какие покупки и сколько можно получить?"},
        {"id": "e3", "role": "employee", "text": "Не знаю."},
        {"id": "c3", "role": "client", "text": "Тогда карта мне не нужна, отменяем встречу."},
        {"id": "e4", "role": "employee", "text": "Понял, передам отказ. Спасибо, до свидания."},
        {"id": "c4", "role": "client", "text": "До свидания."}]
    poor["observations"] = [{"code": "explain_cashback", "turn_id": "e2", "quote": "Много кэшбэка."}]
    good = next(session for name, session, _ in cases() if name == "complete")
    return [("vague_cashback", poor), ("concrete_cost_benefit", good)]


def run():
    if "--live" not in sys.argv:
        print("Прогон API требует --live; без API используйте автоматические тесты.")
        return 0
    load_local_environment()
    provider = CapturingEvaluator()
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "synthetic": True,
              "notice": "Выбранные примеры разработчика; не оценка общей точности.", "cases": [], "status": "running"}
    store = SessionStore(ROOT / "var/grading-ui-check.sqlite3")
    for name, session in prepared_sessions():
        session.update(mode="gigachat", client_reply_mode="scripted", requests={})
        provider.last_evaluation = None
        try:
            updated = apply_evaluation(session, provider)
            store.create(updated)
            by_id = {c["id"]: c["status"] for c in updated["evaluation"]["criteria"]}
            expected = "not_met" if name == "vague_cashback" else "met"
            ok = by_id["C02"] == expected and by_id["C03"] == expected
            entry = {"name": name, "session_id": session["id"], "history": session["history"],
                     "evaluation": updated["evaluation"], "metrics": deepcopy(provider.metrics),
                     "raw": deepcopy(provider.last_evaluation), "ok": ok}
            print(json.dumps({"case": name, "ok": ok, "criteria": by_id, "rating": updated["evaluation"]["rating"]}, ensure_ascii=False), flush=True)
        except Exception as exc:
            entry = {"name": name, "ok": False, "error_type": type(exc).__name__, "error": str(exc),
                     "metrics": deepcopy(provider.metrics), "raw": deepcopy(provider.last_evaluation)}
        report["cases"].append(entry)
        (ROOT / "var/concrete-grading-live.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["status"] = "ok" if all(c["ok"] for c in report["cases"]) else "needs_review"
    (ROOT / "var/concrete-grading-live.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Status:", report["status"], flush=True)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(run())
