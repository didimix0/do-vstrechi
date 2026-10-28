"""Сравнение инструкций на синтетических примерах. Без --live сеть не используется."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from do_vstrechi.client import client_context, validate_client_reply
from do_vstrechi.contracts import ACTION_DESCRIPTIONS, ContractError
from do_vstrechi.data import load_bundle
from do_vstrechi.engine import advance, new_session
from do_vstrechi.gigachat import GigaChatAnalyzer, ProviderError, load_local_environment


class CapturingProvider(GigaChatAnalyzer):
    def _structured(self, *args, **kwargs):
        self.last_raw = super()._structured(*args, **kwargs)
        return self.last_raw


def step(session, text, codes):
    return advance(session, text, {"actions": [{"code": c, "quote": text} for c in codes]}, mode="synthetic_probe")


def seed():
    return step(new_session(load_bundle()), "Добрый день. Я представитель ВТБ, звоню по поводу сегодняшней доставки заказанной дебетовой карты.", ["introduce"])


def client_case(setup):
    session = seed()
    if setup == "listen":
        return step(session, "Можно коротко расскажу об условиях, прежде чем вы решите?", ["offer_brief_explanation"])
    if setup == "passport":
        return step(session, "Паспорт РФ у вас есть?", ["ask_passport_availability"])
    session = step(session, "Обслуживание карты бесплатное.", ["explain_service"])
    if setup == "service_question":
        return session
    if setup == "defer":
        session = step(session, "Выпуск и обслуживание самой карты бесплатны, уведомления оплачиваются отдельно, если подключены. Привычную карту можно оставить, а эту рассмотреть как дополнительную без платы за обслуживание.", ["explain_service_scope", "connect_benefit"])
        session = step(session, "Кешбэк начисляется за подходящие покупки в категориях, которые выбирают каждый месяц.", ["explain_cashback_mechanics"])
        return step(session, "Точный месячный предел сейчас не назову: сначала проверю правила и уточню вам.", ["defer_product_detail"])
    raise ValueError("Unknown synthetic setup")


def matches(codes, case):
    return set(case["required"]) <= set(codes) and not set(case["forbidden"]) & set(codes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Вызывает GigaChat API, расходует токены")
    parser.add_argument("--compare-baseline", action="store_true", help="Также проверить сохранённую предыдущую версию")
    args = parser.parse_args()
    cases = json.loads((ROOT / "scripts/prompt_cases.v1.json").read_text(encoding="utf-8"))
    for case in cases["analysis"]:
        assert case["required"] and not set(case["required"]) & set(case["forbidden"])
        assert set(case["required"] + case["forbidden"]) <= set(ACTION_DESCRIPTIONS)
    for case in cases["client"]:
        assert client_context(client_case(case["setup"]))["reaction"] == case["reaction"]
    if not args.live:
        print(json.dumps({"status": "offline_cases_valid", "analysis_cases": len(cases["analysis"]), "client_cases": len(cases["client"]), "network_used": False}))
        return 0

    load_local_environment()
    provider = CapturingProvider()
    variants = [("current", ROOT / "prompts")]
    if args.compare_baseline:
        variants.insert(0, ("before_guide", ROOT / "prompts/archive/2026-10-17-before-guide"))
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "synthetic": True,
              "notice": cases["notice"], "model": provider.model, "status": "running", "variants": []}
    target = ROOT / "var/prompt-comparison.json"
    target.parent.mkdir(exist_ok=True)

    def save():
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        for name, folder in variants:
            provider.prompt = (folder / "analyze_turn.txt").read_text(encoding="utf-8")
            provider.client_prompt = (folder / "client_role.txt").read_text(encoding="utf-8")
            variant = {"name": name, "prompt_sha256": {filename: hashlib.sha256((folder / filename).read_bytes()).hexdigest() for filename in ["analyze_turn.txt", "client_role.txt"]},
                       "settings": {"analysis_temperature": 0, "analysis_max_tokens": 1000, "client_temperature": 0.7, "client_max_tokens": 450}, "analysis": [], "client": []}
            report["variants"].append(variant)
            for case in cases["analysis"]:
                final = provider.analyze(deepcopy(seed()), case["employee_text"])
                raw_codes = [a["code"] for a in provider.last_raw["actions"]]
                final_codes = [a["code"] for a in final["actions"]]
                result = {"id": case["id"], "employee_text": case["employee_text"], "raw_codes": raw_codes, "final_codes": final_codes,
                          "raw_ok": matches(raw_codes, case), "final_ok": matches(final_codes, case), "metrics": deepcopy(provider.metrics)}
                variant["analysis"].append(result)
                save()
                print(json.dumps({"variant": name, **{k: v for k, v in result.items() if k != "metrics"}}, ensure_ascii=False), flush=True)
            for case in cases["client"]:
                session = client_case(case["setup"])
                reply = provider.generate_client_reply(session)
                context = client_context(session)
                validate_client_reply(provider.last_raw, context)
                result = {"id": case["id"], "reaction": context["reaction"], "employee_text": session["history"][-2]["text"],
                          "client_reply": reply, "contract_ok": True, "manual_check": case["manual_check"], "metrics": deepcopy(provider.metrics)}
                variant["client"].append(result)
                save()
                print(json.dumps({"variant": name, **{k: v for k, v in result.items() if k != "metrics"}}, ensure_ascii=False), flush=True)
        report["status"] = "completed"
        for variant in report["variants"]:
            variant["summary"] = {
                "analysis_raw_passed": sum(x["raw_ok"] for x in variant["analysis"]),
                "analysis_final_passed": sum(x["final_ok"] for x in variant["analysis"]),
                "analysis_total": len(variant["analysis"]),
                "client_contract_passed": sum(x["contract_ok"] for x in variant["client"]),
                "client_total": len(variant["client"]),
                "client_semantics_and_naturalness": "requires_human_review",
            }
        current = report["variants"][-1]
        report["current_expectations_met"] = (
            all(x["raw_ok"] and x["final_ok"] for x in current["analysis"])
            and all(x["contract_ok"] for x in current["client"]))
    except (ProviderError, ContractError) as exc:
        report["status"] = "unavailable" if isinstance(exc, ProviderError) else "contract_error"
        report["error_type"] = type(exc).__name__
        # Настройки окружения, ключи, HTTP-заголовки и низкоуровневые ошибки не записываются.
        print(json.dumps({"status": report["status"], "error_type": type(exc).__name__}), flush=True)
    finally:
        save()
    return 0 if report["status"] == "completed" and report.get("current_expectations_met") else 1


if __name__ == "__main__":
    raise SystemExit(main())
