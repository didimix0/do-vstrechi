"""Учебная проверка оценщика на синтетических разговорах. API только с --live."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from do_vstrechi.contracts import ContractError
from do_vstrechi.data import load_bundle
from do_vstrechi.engine import advance, new_session, review_packet
from do_vstrechi.evaluation import apply_evaluation
from do_vstrechi.gigachat import GigaChatAnalyzer, ProviderError, load_local_environment
from do_vstrechi.webapp import OPTIONS


class CapturingEvaluator(GigaChatAnalyzer):
    def evaluate_session(self, session):
        self.last_evaluation = super().evaluate_session(session)
        return self.last_evaluation


def turn(session, text, codes):
    return advance(session, text, {"actions": [{"code": code, "quote": text} for code in codes]}, mode="synthetic")


def option(session, key):
    text, codes = OPTIONS[key]
    return turn(session, text.format(**session["materials"]["scenario"]["appointment"]), codes)


def cases():
    bundle = load_bundle()
    sessions = []
    for name, keys, ending, expected in [
        ("complete", ["intro", "offer", "service", "benefit", "meeting", "both", "passport"], "appointment", "passed"),
        ("missing_passport", ["intro", "offer", "service", "benefit", "meeting", "both", "goodbye"], "appointment", "retry"),
        ("false_cashback", ["intro", "promise", "goodbye"], "appointment", "retry"),
        ("respected_refusal", ["intro", "offer", "service", "benefit", "meeting", "close"], "decline", "passed")]:
        session = new_session(bundle, ending)
        for key in keys:
            session = option(session, key)
        assert session["status"] == "ended", name
        sessions.append((name, session, expected))
    session = option(new_session(bundle), "intro")
    session = option(session, "promise")
    text = "Исправлю сказанное: кешбэк не начисляется на любые покупки без ограничений. Категории выбирают ежемесячно, возврат идёт за подходящие покупки в выбранных категориях. Общий предел — три тысячи рублей в месяц; это максимум, а не гарантированная выплата."
    session = turn(session, text, ["clarify_product_terms", "explain_cashback", "explain_cashback_mechanics", "explain_cashback_limits"])
    for key in ["service", "benefit", "meeting", "both", "passport"]:
        session = option(session, key)
    assert session["status"] == "ended"
    sessions.append(("explicit_correction", session, "passed_or_requires_review"))
    return sessions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--case", choices=["complete", "missing_passport", "false_cashback", "respected_refusal", "explicit_correction"])
    args = parser.parse_args()
    prepared = cases()
    if args.case:
        prepared = [case for case in prepared if case[0] == args.case]
    if not args.live:
        print(json.dumps({"status": "offline_cases_valid", "synthetic_conversations": len(prepared), "network_used": False}))
        return 0
    load_local_environment()
    provider = CapturingEvaluator()
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "model": provider.model,
              "synthetic": True, "notice": "Набор подготовленных разработчиком учебных разговоров; не оценка общей точности модели.", "cases": []}
    target = ROOT / "var/evaluation-live-check.json"
    target.parent.mkdir(exist_ok=True)
    for name, session, expected in prepared:
        provider.last_evaluation = None
        case = {"name": name, "expected_verdict": expected, "history": deepcopy(session["history"]),
                "material_hashes": session["material_hashes"]}
        try:
            updated = apply_evaluation(session, provider)
            packet = review_packet(updated)
            case.update(actual_verdict=packet["training_verdict"], evaluation=updated["evaluation"], metrics=deepcopy(provider.metrics))
            statuses = {c["id"]: c["status"] for c in packet["criteria"]}
            checks = [packet["training_verdict"] == expected]
            if name == "missing_passport":
                checks.append(statuses["C09"] == "not_met")
            if name == "false_cashback":
                checks.append(any(c["status"] == "contradicted" for c in packet["claims"]))
            if name == "respected_refusal":
                checks.extend([statuses["C07"] == "met", statuses["C08"] == "not_applicable", statuses["C09"] == "not_applicable"])
            if name == "explicit_correction":
                original_id = next(t["id"] for t in session["history"] if t["role"] == "employee" and "без ограничений" in t["text"])
                original = [c for c in packet["claims"] if c["evidence"]["turn_id"] == original_id]
                # До экспертной разметки безопасен и явный запрос ручной проверки.
                # Положительный результат допустим только с сохранённым исправлением.
                checks = [bool(original), packet["training_verdict"] in {"passed", "requires_review"}]
                if packet["training_verdict"] == "passed":
                    checks.append(any(c["status"] == "corrected" for c in original))
                case["expected_verdicts"] = ["passed_with_correction_evidence", "requires_review"]
            case["ok"] = all(checks)
            print(json.dumps({"case": name, "expected": expected, "actual": packet["training_verdict"], "criteria": statuses, "claim_statuses": [c["status"] for c in packet["claims"]], "ok": case["ok"]}, ensure_ascii=False), flush=True)
        except (ProviderError, ContractError) as exc:
            case.update(ok=False, error_type=type(exc).__name__, metrics=deepcopy(provider.metrics))
            if provider.last_evaluation is not None:
                case["rejected_output"] = deepcopy(provider.last_evaluation)
            print(json.dumps({"case": name, "ok": False, "error_type": type(exc).__name__, "safe_message": str(exc)}, ensure_ascii=False), flush=True)
        report["cases"].append(case)
        report["status"] = "running"
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["status"] = "ok" if all(c["ok"] for c in report["cases"]) else "needs_review"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
