"""Контракт разбора деловой речи. Не заменяет смысловую оценку слов сотрудника."""
from copy import deepcopy
from .contracts import ContractError

LEVELS = ("appropriate", "needs_improvement", "unacceptable", "requires_review", "not_applicable")
KINDS = ("familiarity", "slang", "disrespect", "categorical_promise", "unclear_wording", "pressure",
         "profanity", "insult", "humiliation", "threat")
SEVERE_KINDS = {"profanity", "insult", "humiliation", "threat"}


def speech_enabled(session):
    return any(c["id"] == "C10" for c in session["materials"]["rubric"]["criteria"])


def speech_schema(evidence):
    return {"type": "object", "properties": {
        "level": {"type": "string", "enum": list(LEVELS)},
        "summary": {"type": "string", "minLength": 1, "maxLength": 700},
        "findings": {"type": "array", "maxItems": 6, "items": {
            "type": "object", "properties": {
                "kind": {"type": "string", "enum": list(KINDS)},
                "evidence": deepcopy(evidence),
                "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                "suggested_phrase": {"type": "string", "minLength": 1, "maxLength": 500}},
            "required": ["kind", "evidence", "reason", "suggested_phrase"], "additionalProperties": False}}},
        "required": ["level", "summary", "findings"], "additionalProperties": False}


def validate_speech_review(value, bind_evidence):
    if not isinstance(value, dict) or set(value) != {"level", "summary", "findings"}:
        raise ContractError("Неверная структура оценки деловой речи.")
    if (value["level"] not in LEVELS or not isinstance(value["summary"], str)
            or not 1 <= len(value["summary"].strip()) <= 700
            or not isinstance(value["findings"], list) or len(value["findings"]) > 6):
        raise ContractError("Некорректная оценка деловой речи.")
    for finding in value["findings"]:
        if not isinstance(finding, dict) or set(finding) != {"kind", "evidence", "reason", "suggested_phrase"}:
            raise ContractError("Неверная структура замечания к речи.")
        if finding["kind"] not in KINDS or any(not isinstance(finding[k], str) or not 1 <= len(finding[k].strip()) <= 500
                                            for k in ("reason", "suggested_phrase")):
            raise ContractError("Некорректное замечание к речи.")
        bind_evidence(finding["evidence"], True)


def reconcile_speech_review(result, session, by_id, review):
    """Не даёт объявить стиль корректным одновременно с найденным нарушением."""
    if not speech_enabled(session):
        return
    speech = result["speech_review"]
    criterion = by_id["C10"]
    has_employee = any(t["role"] == "employee" for t in session["history"])
    if not has_employee:
        criterion.update(status="not_applicable", reason="В истории нет реплик сотрудника для оценки стиля.", evidence=[], next_step="")
        speech.update(level="not_applicable", summary="Реплик сотрудника для оценки речи нет.", findings=[])
        return
    if speech["level"] == "not_applicable" or criterion["status"] == "not_applicable":
        review("C10", "Реплики сотрудника есть: деловую речь нельзя исключить из оценки.")
    findings = speech["findings"]
    if findings:
        expected = "unacceptable" if any(f["kind"] in SEVERE_KINDS for f in findings) else "needs_improvement"
        if speech["level"] != expected or criterion["status"] != "not_met":
            review("C10", "Уровень деловой речи, найденные замечания и оценка критерия противоречат друг другу; нужна проверка цитат.")
        if not criterion["next_step"]:
            criterion["next_step"] = findings[0]["suggested_phrase"]
    elif speech["level"] in {"needs_improvement", "unacceptable"} or criterion["status"] == "not_met":
        review("C10", "Замечание к речи не подкреплено конкретной цитатой, причиной и корректной заменой.")
    elif speech["level"] == "requires_review" or criterion["status"] == "requires_review":
        review("C10", "Смысл или контекст речи требует дополнительной проверки.")
    elif speech["level"] != "appropriate" or criterion["status"] != "met":
        review("C10", "Не согласованы итог стиля и оценка критерия деловой речи.")
    if criterion["status"] == "requires_review":
        speech.setdefault("model_level", speech["level"])
        speech.update(level="requires_review", summary=criterion["reason"])
