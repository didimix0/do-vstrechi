"""Отбор согласованных образцов речи под разрешённую контроллером реакцию."""
from copy import deepcopy
import json
import re


CHECK_IDS = {f"DV-{n:02d}" for n in range(4, 41, 4)}
ROUTE_KEYS = {"reactions", "flags", "question_ids", "open_questions", "covered_topics",
              "employee_pattern", "employee_not_pattern", "previous_client_pattern",
              "history_pattern", "min_topic_asks", "switched_question", "variant_indices"}


def validate_example_pack(pack, scenario, product, reactions):
    if (pack.get("approved") is not True or pack.get("scenario_id") != scenario["id"]
            or pack.get("product_version") != product["version"] or not pack.get("version")):
        raise ValueError("Образцы должны быть согласованы и соответствовать сценарию и закреплённому продукту.")
    if type(pack.get("max_examples_per_request")) is not int or not 1 <= pack["max_examples_per_request"] <= 3:
        raise ValueError("Число образцов в одном запросе должно быть ограничено.")
    if type(pack.get("max_context_characters")) is not int or not 1000 <= pack["max_context_characters"] <= 8000:
        raise ValueError("Нужен ограниченный объём контекста образцов.")
    cases = pack.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 50:
        raise ValueError("Некорректный набор образцов.")
    seen = set()
    fact_ids = {f["id"] for f in product["facts"]}
    for case in cases:
        code = case.get("id")
        if not isinstance(code, str) or code in seen or code in CHECK_IDS:
            raise ValueError("Повторяющийся образец или контрольная ситуация в рабочем наборе.")
        seen.add(code)
        if (case.get("intended_use") != "prompt_example" or case.get("approval", {}).get("status") != "approved"
                or not set(case.get("product_fact_ids", [])) <= fact_ids):
            raise ValueError("Несогласованный образец или ссылка на неизвестное условие.")
        variants = case["target_client_behavior"]["allowed_reply_variants"]
        if not variants or any(not isinstance(v, str) or not v.strip() for v in variants):
            raise ValueError("Для образца нужны реплики клиента.")
        routes = case.get("runtime_routes")
        if not isinstance(routes, list) or not routes:
            raise ValueError("У образца должны быть явные условия применения.")
        for route in routes:
            if (not isinstance(route, dict) or set(route) - ROUTE_KEYS or not route.get("reactions")
                    or not set(route["reactions"]) <= set(reactions)):
                raise ValueError("Неизвестная реакция или условие отбора образца.")
            for key in ("employee_pattern", "employee_not_pattern", "previous_client_pattern", "history_pattern"):
                if key in route:
                    if not isinstance(route[key], str) or len(route[key]) > 400:
                        raise ValueError("Некорректный фильтр образца.")
                    re.compile(route[key], re.I)
            if any(type(v) is not bool for v in route.get("flags", {}).values()):
                raise ValueError("Неверное условие состояния образца.")
            for i in route.get("variant_indices", range(len(variants))):
                if type(i) is not int or not 0 <= i < len(variants):
                    raise ValueError("Неизвестный вариант реплики образца.")


def select_client_examples(session, context):
    """Контрольные ответы и оценочные пометки сюда не загружаются."""
    scenario = session["materials"]["scenario"]
    if scenario.get("dialogue_features", {}).get("approved_examples") is not True:
        return None
    pack = session["materials"].get("dialogue_examples")
    if not pack or pack.get("approved") is not True or context.get("continuation_reaction"):
        return None
    history = session["history"][:-1]
    employee = next((t["text"] for t in reversed(history) if t["role"] == "employee"), "")
    previous_client = next((t["text"] for t in reversed(history) if t["role"] == "client"), "")
    question_id = context.get("current_question", {}).get("id")
    questions = session.get("product_question_state", {})
    open_questions = [r for r in questions.get("records", []) if r["status"] in {"open", "deferred", "unresolved"}]
    covered = set(questions.get("covered", []))
    topic_asks = max((len(r.get("asked_turn_ids", [])) for r in questions.get("records", [])
                      if r["question_id"] == question_id), default=0)

    def matches(route):
        if context["reaction"] not in route["reactions"]:
            return False
        if "question_ids" in route and question_id not in route["question_ids"]:
            return False
        if any(session["flags"].get(k) is not v for k, v in route.get("flags", {}).items()):
            return False
        if "open_questions" in route and bool(open_questions) is not route["open_questions"]:
            return False
        if not set(route.get("covered_topics", [])) <= covered:
            return False
        if topic_asks < route.get("min_topic_asks", 0):
            return False
        if route.get("switched_question") and not context.get("previous_question_unresolved"):
            return False
        for key, text in (("employee_pattern", employee), ("previous_client_pattern", previous_client),
                          ("history_pattern", "\n".join(t["text"] for t in history))):
            if key in route and not re.search(route[key], text, re.I):
                return False
        return not (route.get("employee_not_pattern") and re.search(route["employee_not_pattern"], employee, re.I))

    candidates = []
    for case in pack["cases"]:
        matching = [r for r in case["runtime_routes"] if matches(r)]
        if matching:
            route = max(matching, key=len)
            variants = case["target_client_behavior"]["allowed_reply_variants"]
            example = {
                "id": case["id"], "context": case["preconditions"],
                "dialogue_before": deepcopy(case["history"]), "employee": case["employee_turn"],
                "possible_client_replies": [variants[i] for i in route.get("variant_indices", range(len(variants)))],
                "behavior": case["target_client_behavior"]["required_meaning"],
            }
            candidates.append((len(route), case["id"], example))
    selected = []
    for _, _, example in sorted(candidates, key=lambda c: (-c[0], c[1])):
        tentative = selected + [example]
        if len(json.dumps(tentative, ensure_ascii=False)) <= pack["max_context_characters"]:
            selected = tentative
        if len(selected) >= pack["max_examples_per_request"]:
            break
    if not selected:
        return None
    return {"version": pack["version"], "examples": selected,
            "instruction": "Это согласованные образцы формулировки. Их предпосылки относятся только к примеру. Текущие required_meaning, current_question, память и состояние контроллера имеют приоритет. Не переноси чужие факты, ошибки сотрудника и решение клиента в текущий звонок."}
