"""Отдельный разбор: модель предлагает оценки, сервер проверяет ссылки и выводит итог."""
from copy import deepcopy
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

from .contracts import ContractError
from .data import fingerprint, passport_question_enabled
from .knowledge import select_product_context
from .grading import CASHBACK, MONEY, RATE, benefit_detail_issue, quantitative_detail_issue, completion_rating
from .speech import speech_enabled, speech_schema, validate_speech_review, reconcile_speech_review
from .benefits import grounded_benefit_dialogue
from .auto_review import automatic_review, review_feedback, has_closing_words
from .detail_policy import detail_policy_enabled,deferral_context,reconcile_deferrals

VERSION = "1.5.0"
CRITERION_STATUSES = ("met", "not_met", "not_applicable", "requires_review")
CLAIM_STATUSES = ("supported", "contradicted", "not_established", "corrected")
INCOMPLETE_ENDINGS = {"client_hangup", "turn_limit", "employee_stopped"}


def input_hash(session):
    return fingerprint({"history": session["history"], "material_hashes": session["material_hashes"],
                        "end_reason": session["end_reason"], "client_outcome": session["client_outcome"]})


def evaluation_context(session):
    if session["status"] != "ended":
        raise ContractError("Оценка доступна после завершения звонка.")
    employee_text = "\n".join(t["text"] for t in session["history"] if t["role"] == "employee")
    product = select_product_context(session["materials"]["product"], employee_text, session["history"])
    facts = [{key: deepcopy(f[key]) for key in ("id", "text", "values", "status", "source_ids") if key in f}
             for f in product["facts"]]
    # У дополнительных предложений нет собственных ID фактов: закрепляем целый блок,
    # включая неизвестные даты, ограничения и расхождения, под отдельным ID.
    import json
    for name, offer in product.get("conditional_offers", {}).items():
        facts.append({"id": "O:" + name, "text": json.dumps(offer, ensure_ascii=False, sort_keys=True),
                      "status": "conditional_offer_with_scope_and_unknowns", "source_ids": offer.get("source_ids", [])})
    scenario = session["materials"]["scenario"]
    context = {
        "history": [{k: t[k] for k in ("id", "role", "text", "type") if k in t} for t in session["history"]],
        "rubric": {"passing_rule": session["materials"]["rubric"]["passing_rule"],
                   "criteria": [{k: deepcopy(c[k]) for k in ("id", "title", "required", "applies_when", "fact_ids", "completion_rule") if k in c}
                                for c in session["materials"]["rubric"]["criteria"]]},
        "scenario": {"brief": scenario["brief"], "appointment": scenario["appointment"],
                     "passport_question_enabled": passport_question_enabled(scenario),
                     "operational_rules": deepcopy(scenario.get("operational_rules", {}))},
        "product": {"name": product["name"], "client_scope": product.get("client_scope"),
                    "facts": facts, "not_established": deepcopy(product.get("not_established", [])),
                    "notice": product.get("notice", "")},
        "end_reason": session["end_reason"],
        "evidence_incomplete": session["end_reason"] in INCOMPLETE_ENDINGS,
        "notice": "Коды промежуточного классификатора не передаются. Оцени саму историю по закреплённым материалам."
    }
    if speech_enabled(session):
        context["speech_policy"] = deepcopy(session["materials"]["rubric"]["speech_policy"])
    if automatic_review(session):
        context['automatic_review_policy'] = {
            'phone_call_only':True,
            'rule':'Оцени краткое объяснение и ответы на реально заданные вопросы. Не требуй полного тарифа или не обсуждавшихся продуктов. Дополнительные подробности допустимо перенести на встречу без выдуманных обещаний.',
            'unknown_claim':'Если собственное обещание сотрудника не подтверждается материалами, отметь not_established и объясни, чего нет в источниках. Для C04/C05 это конкретное замечание, а не запрос проверки человеком. Честное признание незнания само по себе не является ложным обещанием.',
            'decision':'Используй met, not_met или not_applicable с цитатами. Неясный контекст перепроверь по всей истории; не заменяй доказательство догадкой.',
        }
        if session.get('_automatic_review_feedback'):
            context['automatic_review_feedback'] = deepcopy(session['_automatic_review_feedback'])
    if detail_policy_enabled(scenario):
        context['detail_deferral_policy']=deferral_context(session)
        if 'automatic_review_policy' in context:
            context['automatic_review_policy']['rule']='Оцени краткое объяснение и реально заданные вопросы. Только один углублённый вопрос можно честно отложить, обязавшись уточнить и объяснить на встрече. Это не штраф само по себе и не подтверждение неизвестного факта. Базовые условия и все вопросы подряд откладывать нельзя.'
    if grounded_benefit_dialogue(scenario):
        # Только указатели на исходные реплики: ни готового балла, ни оценки достоверности.
        groups = {}
        employee_ids = {t['id'] for t in session['history'] if t['role'] == 'employee'}
        for entry in session.get('product_question_state', {}).get('coverage_evidence', []):
            basis = entry.get('answer_basis', {})
            if basis.get('kind') != 'concrete_product_conditions':
                continue
            ids = groups.setdefault(basis['topic'], [])
            for turn_id in basis['turn_ids']:
                if turn_id in employee_ids and turn_id not in ids:
                    ids.append(turn_id)
        context['related_condition_turns'] = [{'topic':topic, 'turn_ids':ids} for topic, ids in groups.items()]
        context['related_condition_notice'] = 'Это указатели на части объяснения в истории, а не готовые оценки. Для положительного вывода о количественной выгоде укажи все нужные реплики: ставку/сумму, применимость и месячный предел. Самостоятельно проверь смысл, персонализацию и правильность условий.'
    return context


def evaluation_schema(session):
    context = evaluation_context(session)
    string = lambda n: {"type": "string", "maxLength": n}
    evidence = {"type": "object", "properties": {
        "turn_id": {"type": "string", "enum": [t["id"] for t in context["history"]]}},
        "required": ["turn_id"], "additionalProperties": False}
    criterion = {"type": "object", "properties": {
        "status": {"type": "string", "enum": list(CRITERION_STATUSES)},
        "reason": {"type": "string", "minLength": 1, "maxLength": 700},
        "next_step": string(500),
        "evidence": {"type": "array", "maxItems": 6, "items": evidence}},
        "required": ["status", "reason"], "additionalProperties": False}
    claim = {"type": "object", "properties": {
        "evidence": evidence, "status": {"type": "string", "enum": list(CLAIM_STATUSES)},
        "explanation": {"type": "string", "minLength": 1, "maxLength": 700},
        "fact_ids": {"type": "array", "maxItems": 4, "items": {"type": "string", "enum": [f["id"] for f in context["product"]["facts"]]}},
        "correction": {"type": "array", "maxItems": 1, "items": evidence}},
        "required": ["evidence", "status", "explanation", "fact_ids"], "additionalProperties": False}
    ids = [c["id"] for c in context["rubric"]["criteria"]]
    schema = {"type": "object", "properties": {
        "criteria": {"type": "object", "properties": {code: criterion for code in ids},
                     "required": ids, "additionalProperties": False},
        "claims": {"type": "array", "maxItems": 24, "items": claim},
        "coverage_complete": {"type": "boolean"}, "coverage_note": string(700)},
        "required": ["criteria", "claims", "coverage_complete", "coverage_note"], "additionalProperties": False}
    if speech_enabled(session):
        schema["properties"]["speech_review"] = speech_schema(evidence)
        schema["required"].append("speech_review")
    return schema


def _keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ContractError("Оценщик вернул неожиданные поля.")


def _text(value, maximum, minimum=0):
    if not isinstance(value, str) or not minimum <= len(value) <= maximum or (minimum and not value.strip()):
        raise ContractError("Некорректный текст в оценке.")


def _list(value, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ContractError("Некорректный список в оценке.")


def validate_evaluation(payload, session):
    """Проверяет структуру и происхождение цитат; не доказывает смысловые решения LLM."""
    expected_keys = ["criteria", "claims", "coverage_complete", "coverage_note"]
    if speech_enabled(session):
        expected_keys.append("speech_review")
    _keys(payload, expected_keys)
    payload = deepcopy(payload)
    # На проводе критерии — объект с фиксированными ключами. В хранилище и UI —
    # упорядоченный список. Значения оценок и отсутствующие цитаты не додумываются.
    if isinstance(payload["criteria"], dict):
        ids = [c["id"] for c in session["materials"]["rubric"]["criteria"]]
        if set(payload["criteria"]) != set(ids):
            raise ContractError("Оценены не все критерии или появились лишние.")
        for value in payload["criteria"].values():
            if not isinstance(value, dict) or "id" in value:
                raise ContractError("Некорректная запись критерия.")
        payload["criteria"] = [{"id": code, **payload["criteria"][code]} for code in ids]
    if isinstance(payload["criteria"], list):
        for criterion in payload["criteria"]:
            if isinstance(criterion, dict):
                criterion.setdefault("evidence", [])
                criterion.setdefault("next_step", "")
    if isinstance(payload["claims"], list):
        for claim in payload["claims"]:
            if isinstance(claim, dict) and claim.get("status") != "corrected":
                claim.setdefault("correction", [])
    if type(payload["coverage_complete"]) is not bool:
        raise ContractError("Не указана полнота проверки.")
    _text(payload["coverage_note"], 700)
    context = evaluation_context(session)
    turns = {t["id"]: t for t in context["history"]}
    order = {t["id"]: i for i, t in enumerate(context["history"])}
    facts = {f["id"]: f for f in context["product"]["facts"]}
    criteria = {c["id"]: c for c in context["rubric"]["criteria"]}

    def evidence(value, employee_only=False):
        if isinstance(value, dict) and set(value) == {"turn_id"}:
            if not isinstance(value["turn_id"], str) or value["turn_id"] not in turns:
                raise ContractError("Цитата ссылается на неизвестную реплику.")
            value["quote"] = turns[value["turn_id"]]["text"]
        _keys(value, ("turn_id", "quote"))
        if not isinstance(value["turn_id"], str) or value["turn_id"] not in turns:
            raise ContractError("Цитата ссылается на неизвестную реплику.")
        _text(value["quote"], 4000, 1)
        turn = turns[value["turn_id"]]
        if value["quote"] not in turn["text"] or (employee_only and turn["role"] != "employee"):
            raise ContractError("Цитата не подтверждается исходными словами сотрудника.")

    if speech_enabled(session):
        validate_speech_review(payload["speech_review"], evidence)
    _list(payload["criteria"], len(criteria))
    seen = set()
    for c in payload["criteria"]:
        _keys(c, ("id", "status", "reason", "next_step", "evidence"))
        if not isinstance(c["id"], str) or c["id"] not in criteria or c["id"] in seen:
            raise ContractError("Неизвестный или повторяющийся критерий.")
        seen.add(c["id"])
        # Уровень речи и статус критерия имеют однозначное соответствие только
        # в C10. Сохраняем исходный статус; чужие/неизвестные статусы не принимаем.
        wire_status = None
        aliases = {"appropriate": "met", "needs_improvement": "not_met", "unacceptable": "not_met"}
        if (c["id"] == "C10" and c["status"] in aliases
                and c["status"] == payload.get("speech_review", {}).get("level")):
            wire_status = c["status"]
            c["status"] = aliases[wire_status]
        if c["status"] not in CRITERION_STATUSES:
            raise ContractError("Неизвестный статус критерия.")
        _text(c["reason"], 700, 1)
        _text(c["next_step"], 500)
        _list(c["evidence"], 6)
        for e in c["evidence"]:
            evidence(e)
        if c["status"] == "met" and not any(turns[e["turn_id"]]["role"] == "employee" for e in c["evidence"]):
            c.update(model_status="met", status="requires_review",
                     reason="Оценщик не указал реплику сотрудника, подтверждающую выполнение этого критерия. Положительная оценка требует проверки по истории.")
        if wire_status is not None:
            c["wire_status"] = wire_status
    if seen != set(criteria):
        raise ContractError("Оценены не все критерии.")
    _list(payload["claims"], 24)
    for claim in payload["claims"]:
        if isinstance(claim, dict) and "fact_ids" in claim:
            _keys(claim, ("evidence", "status", "explanation", "fact_ids", "correction"))
            _list(claim["fact_ids"], 4)
            if any(not isinstance(code, str) or code not in facts for code in claim["fact_ids"]):
                raise ContractError("В оценке появился неизвестный факт.")
            claim["facts"] = [{"fact_id": code, "quote": facts[code]["text"]} for code in dict.fromkeys(claim.pop("fact_ids"))]
        _keys(claim, ("evidence", "status", "explanation", "facts", "correction"))
        evidence(claim["evidence"], True)
        if claim["status"] not in CLAIM_STATUSES:
            raise ContractError("Неизвестный статус утверждения.")
        _text(claim["explanation"], 700, 1)
        _list(claim["facts"], 4)
        for ref in claim["facts"]:
            _keys(ref, ("fact_id", "quote"))
            if not isinstance(ref["fact_id"], str) or ref["fact_id"] not in facts:
                raise ContractError("В оценке появился неизвестный факт.")
            _text(ref["quote"], 50000, 8)
            if ref["quote"] not in facts[ref["fact_id"]]["text"]:
                raise ContractError("Цитата условия отсутствует в материалах продукта.")
        if claim["status"] != "not_established" and not claim["facts"]:
            claim["status"] = "not_established"
            claim["explanation"] = "Оценщик не указал источник для этого вывода. Утверждение требует проверки; его нельзя считать подтверждённым или опровергнутым."
        # Льгота для отдельной категории не становится условием обычной карты.
        if (claim["status"] == "supported" and any(f["fact_id"] == "P15" for f in claim["facts"])
                and not re.search(r"зарплат|пенси", claim["evidence"]["quote"], re.I)):
            claim["status"] = "not_established"
            claim["explanation"] = "Указанное условие снятия относится к зарплатным и пенсионным клиентам. Принадлежность клиента к этой категории и применимость условия не установлены."
        _list(claim["correction"], 1)
        if claim["status"] == "corrected" and not claim["correction"]:
            claim["status"] = "not_established"
            claim["explanation"] = "Оценщик назвал утверждение исправленным, но не указал последующую реплику. Исправление требует проверки по истории разговора."
        for correction in claim["correction"]:
            evidence(correction, True)
            if order[correction["turn_id"]] <= order[claim["evidence"]["turn_id"]]:
                raise ContractError("Исправление должно следовать после ошибочного утверждения.")
        if claim["correction"] and claim["status"] not in {"corrected", "not_established"}:
            claim["status"] = "not_established"
            claim["explanation"] = "Оценщик указал последующее исправление, но его статус не согласован с этой ссылкой. Нужна проверка исходного обещания и последующего уточнения."
    return deepcopy(payload)


def _complete_related_evidence(criterion, session, check):
    """Дополняет лишь конкретную цитату частями того же объяснения.

    Это не пересмотр смысловой оценки модели. Общий слоган, чужая тема,
    неизвестная реплика и отрицательная оценка не превращаются в зачёт.
    """
    if not grounded_benefit_dialogue(session['materials']['scenario']):
        return
    employee = {t['id']: t['text'] for t in session['history'] if t['role']=='employee'}
    anchors = {e['turn_id'] for e in criterion['evidence'] if e['turn_id'] in employee
               and CASHBACK.search(e['quote']) and (RATE.search(e['quote']) or MONEY.search(e['quote']))}
    if not anchors:
        return
    extra = []
    for group in evaluation_context(session).get('related_condition_turns', []):
        if group['topic'] == 'cashback' and anchors.intersection(group['turn_ids']):
            extra.extend(group['turn_ids'])
    combined = deepcopy(criterion['evidence'])
    known = {e['turn_id'] for e in combined}
    for turn_id in extra:
        if turn_id not in known and len(combined) < 6:
            combined.append({'turn_id':turn_id, 'quote':employee[turn_id]})
            known.add(turn_id)
    if len(combined) > len(criterion['evidence']) and check('\n'.join(e['quote'] for e in combined)) is None:
        criterion['model_evidence'] = deepcopy(criterion['evidence'])
        criterion['evidence'] = combined
        criterion['evidence_context_added'] = True


def finalize_evaluation(payload, session):
    result = validate_evaluation(payload, session)
    by_id = {c["id"]: c for c in result["criteria"]}
    warnings = []

    def review(code, reason):
        if code in by_id:
            c = by_id[code]
            c.setdefault("model_status", c["status"])
            c["status"] = "requires_review"
            c["reason"] = reason
        if reason not in warnings:
            warnings.append(reason)

    reconcile_speech_review(result, session, by_id, review)
    # Применимость обязательных шагов нельзя отменить только ответом модели.
    if "C01" in by_id and by_id["C01"]["status"] == "not_applicable":
        review("C01", "Начало звонка обязательно оценивается; неприменимость требует проверки.")
    if ("C06" in by_id and by_id["C06"]["status"] == "not_applicable"
            and session["end_reason"] not in INCOMPLETE_ENDINGS | {"client_unresolved_questions"}):
        refusal_position = next((i for i,t in enumerate(session['history']) if t.get('reaction') in {'decline','rude_decline','firm_refusal'}),None)
        later_employee = [t for i,t in enumerate(session['history']) if t['role']=='employee' and refusal_position is not None and i>refusal_position]
        if automatic_review(session) and later_employee and not any(has_closing_words(t['text']) for t in later_employee):
            by_id['C06'].update(status='not_met',reason='После окончательного отказа сотрудник продолжил разговор без принятия отказа или корректного завершения.',
                                evidence=[{'turn_id':later_employee[-1]['id'],'quote':later_employee[-1]['text']}],next_step='Примите окончательный отказ и спокойно завершите звонок.')
        else:
            review("C06", "В завершённом звонке нужно оценить предложение следующего шага или корректное завершение.")
    agreed = session["client_outcome"] in {"ready_to_meet", "appointment_agreed"}
    refused = any(t.get("reaction") in {"decline", "rude_decline", "firm_refusal"} for t in session["history"])
    opportunity = any(t.get("reaction") in {"objection", "listen", "reason", "reason_and_needs"} for t in session["history"])
    if opportunity and session["end_reason"] not in INCOMPLETE_ENDINGS:
        for code in ("C02", "C03"):
            if code in by_id and by_id[code]["status"] == "not_applicable":
                review(code, "Клиент выразил сомнение и допускал обсуждение: нужно оценить работу с возражением.")
    if agreed and session["end_reason"] not in INCOMPLETE_ENDINGS:
        for code in ("C08", "C09"):
            if code == "C09" and not passport_question_enabled(session["materials"]["scenario"]):
                continue
            if code in by_id and by_id[code]["status"] == "not_applicable":
                review(code, "При сохранённой встрече этот обязательный шаг применим.")
    if refused and "C07" in by_id and by_id["C07"]["status"] == "not_applicable":
        review("C07", "В истории есть окончательный отказ: нужно проверить реакцию сотрудника.")
    if automatic_review(session) and refused and 'C07' in by_id:
        refusal_at = next(i for i,t in enumerate(session['history']) if t.get('reaction') in {'decline','rude_decline','firm_refusal'})
        repeated_pitch = []
        for i,turn in enumerate(session['history']):
            if i<=refusal_at or turn['role']!='employee':
                continue
            codes = {o['code'] for o in session['observations'] if o['turn_id']==turn['id']}
            selling = codes & {'explain_service','explain_cashback','connect_benefit','explain_service_scope','explain_cashback_mechanics','explain_cashback_limits','possible_product_error','pressure','offer_brief_explanation','propose_next_step'}
            correction = 'clarify_product_terms' in codes or re.search(r'исправ|поправ|ошиб|уточню',turn['text'],re.I)
            if selling and not correction and not has_closing_words(turn['text']):
                repeated_pitch.append(turn)
        if repeated_pitch:
            by_id['C07'].update(status='not_met',reason='После окончательного отказа сотрудник продолжил перечислять преимущества или обещания вместо принятия решения клиента.',
                                evidence=[{'turn_id':t['id'],'quote':t['text']} for t in repeated_pitch[:6]],next_step='После окончательного отказа прекратите продажу, примите решение клиента и завершите звонок.')
    client_turns = [t for t in session["history"] if t["role"] == "client" and t["id"] != "opening"]
    if not refused and client_turns and all("reaction" in t for t in client_turns) and "C07" in by_id:
        by_id["C07"].update(status="not_applicable", reason="В истории этого упражнения не было окончательного отказа клиента.")
    if automatic_review(session) and not agreed:
        for code in ('C08','C09'):
            if code in by_id:
                by_id[code].update(status='not_applicable', reason='Согласованной встречи нет: этот шаг в данном разговоре не применялся.', evidence=[], next_step='')
    if "C09" in by_id and not passport_question_enabled(session["materials"]["scenario"]):
        by_id["C09"].update(status="not_applicable", reason="В закреплённой версии упражнения вопрос о паспорте отключён.")
    elif "C09" in by_id and by_id["C09"]["status"] == "met":
        passport_quotes = [e["quote"] for e in by_id["C09"]["evidence"]
                           if next(t for t in session["history"] if t["id"] == e["turn_id"])["role"] == "employee"
                           and re.search(r"\bпаспорт", e["quote"], re.I)]
        question_form = r"\?|\b(?:есть|будет|имеется)\s+ли\b|\b(?:возьм[её]те|сможете|получится)\b|\bу вас.{0,50}\bпаспорт|\bпаспорт.{0,50}\b(?:у вас|будет|есть)"
        if not any(re.search(question_form, q, re.I) for q in passport_quotes):
            review("C09", "Цитата не показывает вопрос о наличии паспорта; простого напоминания недостаточно.")
    reconcile_deferrals(result,session,by_id,review)
    statuses = {claim["status"] for claim in result["claims"]}
    # Короткая рекламная фраза не становится объяснением из-за положительного ответа LLM.
    employee_ids = {t["id"] for t in session["history"] if t["role"] == "employee"}
    all_employee_text = "\n".join(t["text"] for t in session["history"] if t["role"] == "employee")
    for code in ("C02", "C03", "C04"):
        if code not in by_id or by_id[code]["status"] != "met":
            continue
        c = by_id[code]
        quoted = "\n".join(e["quote"] for e in c["evidence"] if e["turn_id"] in employee_ids)
        check = quantitative_detail_issue if code == "C04" else benefit_detail_issue
        issue = check(quoted)
        if issue:
            _complete_related_evidence(c, session, check)
            quoted = '\n'.join(e['quote'] for e in c['evidence'] if e['turn_id'] in employee_ids)
            issue = check(quoted)
        if not issue:
            continue
        if check(all_employee_text) is None:
            review(code, "Выбранная цитата не объясняет выгоду достаточно конкретно. В истории есть дополнительные условия: нужно проверить их связь с этим критерием.")
        else:
            c.update(model_status="met", status="not_met", reason=issue,
                     next_step="Раскройте пользу конкретными условиями: для кешбэка — ставка или сумма, применимые покупки и месячный предел; для платной услуги — стоимость и период. Свяжите объяснение с сомнением клиента; значения сверьте с материалами.")
    product_discussed = any(o["code"] in {"explain_service", "explain_cashback", "explain_service_scope", "connect_benefit",
                                        "explain_cashback_mechanics", "explain_cashback_limits", "possible_product_error"}
                            for o in session["observations"])
    if product_discussed and not result["claims"]:
        review("C04", "В разговоре обсуждались условия, но оценщик не выделил утверждения для сверки.")
    flagged_turns = {o["turn_id"] for o in session["observations"] if o["code"] == "possible_product_error"}
    checked_turns = {c["evidence"]["turn_id"] for c in result["claims"]}
    # Отсутствие неподтверждённых обещаний доказывается совокупностью проверенных
    # утверждений. Оценщик может не выбрать единственную цитату для такого отсутствия.
    if (automatic_review(session) and result['coverage_complete'] and result['claims']
            and not (flagged_turns-checked_turns) and statuses <= {'supported','corrected'}
            and by_id.get('C05',{}).get('model_status')=='met'
            and not by_id['C05']['evidence']):
        by_id['C05'].update(status='met', reason='Все выделенные продуктовые утверждения подтверждены материалами либо явно исправлены; неподтверждённых обещаний не осталось.',
                            evidence=[deepcopy(c['evidence']) for c in result['claims'][:6]],next_step='')
    if flagged_turns - checked_turns:
        review("C04", "Оценщик пропустил ранее выделенное спорное утверждение; нужна отдельная сверка, даже если далее было уточнение.")
    if result["claims"]:
        for code in ("C04", "C05"):
            if code in by_id and by_id[code]["status"] == "not_applicable":
                review(code, "В разговоре есть продуктовые утверждения: неприменимость требует проверки.")
    if "contradicted" in statuses:
        for code in ("C02", "C03"):
            if code in by_id and by_id[code]["status"] == "met":
                employee_ids = {t["id"] for t in session["history"] if t["role"] == "employee"}
                evidence = [e for e in by_id[code]["evidence"] if e["turn_id"] in employee_ids]
                contradicted = [c["evidence"] for c in result["claims"] if c["status"] == "contradicted"]
                if evidence and all(any(e["turn_id"] == claim["turn_id"] and e["quote"] in claim["quote"] for claim in contradicted) for e in evidence):
                    by_id[code].update(model_status="met", status="not_met", reason="Объяснение опирается только на утверждение, противоречащее материалам.", next_step="Ответьте на сомнение клиента через подтверждённые условия карты.")
        for code in ("C04", "C05"):
            if code in by_id and by_id[code]["status"] == "met":
                if automatic_review(session):
                    by_id[code].update(status='not_met', reason='В разговоре осталось утверждение, противоречащее условиям продукта.',
                                       next_step='Исправьте ошибочное условие и назовите значение из материалов продукта.')
                else:
                    review(code, "Есть неисправленное противоречие материалам; положительная оценка требует проверки.")
    if "not_established" in statuses:
        for code in ("C04", "C05"):
            if automatic_review(session):
                uncertain = [claim for claim in result['claims'] if claim['status']=='not_established']
                by_id[code].update(status='not_met', reason='Собственное обещание не подтверждается материалами продукта. ' + uncertain[0]['explanation'],
                                   next_step='Назовите только подтверждённые условия либо прямо уточните, что это обещание пока подтвердить нельзя.')
                by_id[code]['evidence'] = [deepcopy(claim['evidence']) for claim in uncertain[:6]]
            else:
                review(code, "Часть условий или исправлений не подтверждена достаточными свидетельствами; нужна проверка.")
    if not result["coverage_complete"]:
        warnings.append("Оценщик отметил неполноту проверки утверждений.")
    if session["end_reason"] in INCOMPLETE_ENDINGS and not automatic_review(session):
        warnings.append("Разговор прерван или ограничен: общий результат требует проверки по доступным репликам.")
    required = [by_id[c["id"]] for c in session["materials"]["rubric"]["criteria"] if c["required"]]
    if automatic_review(session):
        unresolved_reasons = {c['reason'] for c in result['criteria'] if c['status']=='requires_review'}
        warnings[:] = [w for w in warnings if w in unresolved_reasons or not result['coverage_complete']]
    if warnings or any(c["status"] == "requires_review" for c in required):
        verdict = "requires_review"
    elif any(c["status"] == "not_met" for c in required) or "contradicted" in statuses:
        verdict = "retry"
    else:
        verdict = "passed"
    result.update(status="completed", version=VERSION, input_hash=input_hash(session), training_verdict=verdict,
                  warnings=warnings, created_at=datetime.now(timezone.utc).isoformat(),
                  notice="Предварительная учебная оценка по сохранённым материалам. Смысловые решения модели могут потребовать проверки наставником.")
    if automatic_review(session):
        result['notice'] = 'Разбор по репликам звонка и сохранённым условиям продукта. Показаны выполненные шаги и конкретные замечания.'
    result["rating"] = completion_rating(result["criteria"], provisional=bool(warnings))
    return result


def session_rating(session):
    evaluation = session.get("evaluation")
    if not evaluation or evaluation.get("input_hash") != input_hash(session):
        return None
    return completion_rating(evaluation["criteria"], provisional=bool(evaluation.get("warnings")) or (session["end_reason"] in INCOMPLETE_ENDINGS and not automatic_review(session)))


def fact_sources(session, ref):
    """Адреса и названия источников берёт сервер из материалов, а не из ответа LLM."""
    product = session["materials"]["product"]
    context = evaluation_context(session)
    fact = next(f for f in context["product"]["facts"] if f["id"] == ref["fact_id"])
    sources = []
    for source in product.get("sources", []):
        if source["id"] not in fact.get("source_ids", []):
            continue
        entry = {"id": source["id"], "title": source.get("title", source["id"])}
        urls = [source.get("url", "")] + source.get("declared_urls", [])
        entry["urls"] = [u for u in urls if urlsplit(u).scheme == "https" and
                         (urlsplit(u).hostname or "").lower() in {"vtb.ru", "www.vtb.ru"}]
        original_fact = next((f for f in product["facts"] if f["id"] == ref["fact_id"]), {})
        entry["locations"] = [{k: value[k] for k in ("sheet", "cell", "quote") if k in value}
                              for value in original_fact.get("evidence", []) if value.get("source_id") == source["id"]]
        sources.append(entry)
    return {**deepcopy(ref), "sources": sources}


def apply_evaluation(session, provider):
    """Спорный результат повторно проверяется до выдачи законченного разбора."""
    calls = []
    working = deepcopy(session)
    maximum = 2 if automatic_review(session) else 1
    for attempt in range(maximum):
        try:
            raw = provider.evaluate_session(working)
            evaluation = finalize_evaluation(raw, session)
        except ContractError as exc:
            if attempt+1 >= maximum:
                raise
            calls.append({**deepcopy(provider.metrics), 'role':'session_evaluation','status':'error',
                          'automatic_attempt':attempt+1,'validation_detail':str(exc)})
            working = deepcopy(session)
            working['_automatic_review_feedback'] = {'issues':[str(exc)],'instruction':'Верни полный разбор с допустимыми статусами и точными ссылками только на существующие реплики и факты.'}
            continue
        calls.append({**deepcopy(provider.metrics), 'role':'session_evaluation', 'status':'ok',
                      'automatic_attempt':attempt+1, 'at':evaluation['created_at']})
        if evaluation['training_verdict'] != 'requires_review' or not automatic_review(session):
            break
        working = deepcopy(session)
        working['_automatic_review_feedback'] = review_feedback(evaluation)
    if automatic_review(session) and evaluation['training_verdict']=='requires_review':
        provider.metrics['automatic_evaluation_attempts'] = calls
        provider.metrics['unresolved_feedback'] = review_feedback(evaluation)
        raise ContractError('Автоматический разбор не завершён: оценщик не смог согласовать оценки и исходные цитаты. Повторите автооценку.')
    evaluation['automatic_verification_attempts'] = len(calls)
    provider.metrics["status"] = "ok"
    updated = deepcopy(session)
    updated["evaluation"] = evaluation
    updated["training_verdict"] = evaluation["training_verdict"]
    updated.pop("evaluation_error", None)
    updated['calls'].extend(calls)
    return updated
