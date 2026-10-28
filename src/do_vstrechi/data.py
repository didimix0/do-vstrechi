from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
from .contracts import ACTION_DESCRIPTIONS
from .questions import product_questions_enabled
from .dialogue_examples import validate_example_pack
from .opening import employee_starts, starts_with_greeting

ROOT = Path(__file__).resolve().parents[2]


def fingerprint(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def passport_question_enabled(scenario: dict) -> bool:
    """Возможность закреплена в материалах: прежние попытки сохраняют свою логику."""
    return scenario.get("dialogue_features", {}).get("passport_question") is True


def validate_bundle(bundle: dict) -> None:
    scenario, product, rubric = (bundle[k] for k in ("scenario", "product", "rubric"))
    for obj in (scenario, product, rubric):
        if not isinstance(obj.get("id"), str) or not obj["id"] or not obj.get("version"):
            raise ValueError("У материалов должны быть ID и версия.")
    if scenario["product_id"] != product["id"] or scenario["rubric_id"] != rubric["id"]:
        raise ValueError("Сценарий ссылается на другую версию набора материалов.")
    if type(scenario["max_turns"]) is not int or not 1 <= scenario["max_turns"] <= 50:
        raise ValueError("Некорректный лимит реплик.")
    if scenario.get("channel") != "phone":
        raise ValueError("Текущий контроллер поддерживает только звонок перед встречей.")
    expected = {"objection", "listen", "reason", "needs", "service_explained", "cashback_question",
                "ready_to_schedule", "missing_explanation", "missing_service", "appointment",
                "decline", "rude_decline", "source_question", "firm_refusal", "closed", "clarify",
                "ask_time", "ask_address"}
    if passport_question_enabled(scenario):
        expected |= {"details_confirmed", "passport_available", "passport_objection", "passport_appointment"}
        if scenario["persona"].get("original_passport_available") is not True:
            raise ValueError("В текущем варианте упражнения нужно явно задать наличие оригинала паспорта у учебного клиента.")
    if product_questions_enabled(scenario):
        expected |= {"detail_answered", "deferred_detail_ack"}
        if scenario.get("dialogue_features", {}).get("question_patience") is True:
            expected |= {"unanswered_goodbye", "unresolved_ack"}
            policy = scenario.get("question_policy", {})
            if (set(policy) != {"max_asks_per_topic", "max_unresolved_topics"}
                    or any(type(v) is not int or not 1 <= v <= 3 for v in policy.values())):
                raise ValueError("Нужны ограниченные число уточнений и число незакрытых тем.")
        if scenario.get('dialogue_features', {}).get('grounded_benefit_dialogue') is True:
            expected.add('partial_answer_ack')
    if not expected <= set(scenario["replies"]):
        raise ValueError("В сценарии отсутствуют обязательные реакции.")
    if any(not isinstance(v, str) or not v.strip() for v in scenario["replies"].values()):
        raise ValueError("Реплики клиента должны быть непустыми строками.")
    profiles = scenario.get('client_profiles', [])
    if not isinstance(profiles, list) or len({p.get('id') for p in profiles}) != len(profiles):
        raise ValueError('Характеры клиента должны иметь уникальные идентификаторы.')
    for profile in profiles:
        if (any(not isinstance(profile.get(k),str) or not profile[k].strip() for k in ('id','label','description','instruction','opening'))
                or not starts_with_greeting(profile['opening'])
                or not isinstance(profile.get('replies'),dict)
                or not set(profile['replies']) <= set(scenario['replies'])
                or any(not isinstance(v,str) or not v.strip() for v in profile['replies'].values())):
            raise ValueError('Нужны описание характера и допустимые реплики с приветствием.')
    if scenario.get('refusal_policy'):
        refusal = scenario['refusal_policy']
        if (type(refusal.get('max_reminders')) is not int or refusal.get('max_reminders') != 1
                or any(not isinstance(refusal.get(k),str) or not refusal[k].strip() for k in ('reminder','closing'))
                or len({scenario['replies']['firm_refusal'],refusal['reminder'],refusal['closing']}) != 3):
            raise ValueError('После окончательного отказа нужно одно отличающееся напоминание и отдельное завершение.')
    if employee_starts(scenario):
        if scenario.get("opening") not in (None, ""):
            raise ValueError("При первом ходе сотрудника автоматическая реплика клиента не нужна.")
        if (not isinstance(scenario.get("first_objection_reply"), str)
                or not starts_with_greeting(scenario["first_objection_reply"])):
            raise ValueError("Первый ответ на представление должен начинаться с приветствия.")
    elif not isinstance(scenario.get("opening"), str) or not scenario["opening"].strip():
        raise ValueError("Нужна начальная реплика клиента.")
    if not scenario.get("event_descriptions", {}).get("client_hangup"):
        raise ValueError("Нужно описание события сброса звонка.")
    appointment = scenario.get("appointment", {})
    if appointment.get("synthetic") is not True or any(
            not isinstance(appointment.get(key), str) or not appointment[key].strip()
            for key in ("date_label", "time", "address")):
        raise ValueError("Нужна вымышленная учебная карточка с датой, временем и адресом.")
    fact_ids = [f["id"] for f in product["facts"]]
    source_ids = [s["id"] for s in product.get("sources", [])]
    if not source_ids or len(set(source_ids)) != len(source_ids):
        raise ValueError("У паспорта должны быть источники с уникальными ID.")
    for fact in product["facts"]:
        if not fact.get("source_ids") or not set(fact["source_ids"]) <= set(source_ids):
            raise ValueError("Факт должен ссылаться на существующие источники паспорта.")
    def validate_offer_sources(value):
        if isinstance(value, dict):
            if "source_ids" in value and not set(value["source_ids"]) <= set(source_ids):
                raise ValueError("Предложение ссылается на отсутствующий источник.")
            if "source_id" in value and value["source_id"] not in source_ids:
                raise ValueError("Выдержка ссылается на отсутствующий источник.")
            for item in value.values():
                validate_offer_sources(item)
        elif isinstance(value, list):
            for item in value:
                validate_offer_sources(item)
    validate_offer_sources(product)
    criterion_ids = [c["id"] for c in rubric["criteria"]]
    if product_questions_enabled(scenario):
        questions = scenario.get("product_questions", [])
        if not questions or len({q["id"] for q in questions}) != len(questions):
            raise ValueError("Нужны уникальные определения уточняющих вопросов.")
        for question in questions:
            if (not question.get("text") or not set(question["fact_ids"]) <= set(fact_ids)
                    or not set(question["criterion_ids"]) <= set(criterion_ids)
                    or not question.get("answer_actions")
                    or not set(question["answer_actions"]) <= set(ACTION_DESCRIPTIONS)
                    or question["trigger"] not in {"any_explanation", "service_discussed", "cashback_discussed", "client_follow_up", "client_advanced_follow_up"}):
                raise ValueError("Уточняющий вопрос содержит неверную ссылку или условие.")
            if question.get('difficulty')=='advanced' and (not question.get('answer_patterns') or not question.get('profiles')):
                raise ValueError('Для углублённого вопроса нужны признаки ответа и профиль клиента.')
            for pattern in question.get('answer_patterns',[]):
                re.compile(pattern)
            if not set(question.get('requires_covered',[])) <= {q['id'] for q in questions}:
                raise ValueError('Вопрос ссылается на неизвестную предварительную тему.')
        if scenario.get('dialogue_features',{}).get('distinct_client_behavior'):
            if scenario.get('detail_deferral_policy')!={'maximum_accepted':1,'advanced_only':True,'requires_check_and_meeting_explanation':True}:
                raise ValueError('Допускается один перенос углублённого вопроса с обещанием уточнить и объяснить.')
            if not scenario['replies'].get('meeting_detail_ack'):
                raise ValueError('Нужна реакция на принятый перенос сложного вопроса.')
    if len(set(fact_ids)) != len(fact_ids) or len(set(criterion_ids)) != len(criterion_ids):
        raise ValueError("Повторяются ID фактов или критериев.")
    for criterion in rubric["criteria"]:
        if not set(criterion["fact_ids"]) <= set(fact_ids):
            raise ValueError("Критерий ссылается на отсутствующий факт.")
        if not set(criterion["candidate_actions"]) <= set(ACTION_DESCRIPTIONS):
            raise ValueError("В критерии есть неизвестное действие.")
    if scenario.get("dialogue_features", {}).get("approved_examples") is True:
        from .client import REACTIONS
        validate_example_pack(bundle.get("dialogue_examples", {}), scenario, product, REACTIONS)
    if "C10" in criterion_ids and not rubric.get("speech_policy", {}).get("version"):
        raise ValueError("Для критерия деловой речи нужны согласованные нормы.")


def load_bundle(root: Path = ROOT) -> dict:
    files = {"scenario": "scenarios/premeeting_call.v1.json",
             "product": "products/vtb_debit.v1.json", "rubric": "rubrics/premeeting_call.v1.json"}
    bundle = {k: json.loads((root / "data" / file).read_text(encoding="utf-8")) for k, file in files.items()}
    if bundle["scenario"].get("dialogue_features", {}).get("approved_examples") is True:
        bundle["dialogue_examples"] = json.loads((root / "data/dialogue_examples/premeeting_call.v1.json").read_text(encoding="utf-8"))
    catalog_reference = bundle["product"].get("supplementary_catalog")
    if catalog_reference:
        products_folder = (root / "data/products").resolve()
        catalog_path = (root / catalog_reference).resolve()
        if products_folder not in catalog_path.parents:
            raise ValueError("Каталог предложений должен находиться в data/products.")
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        bundle["product"]["conditional_offers"] = {key: catalog[key] for key in (
            "cashback_snapshot", "grow_with_vtb", "family_and_close", "partner_offers", "payment_tools", "VTB_Plus")}
        if "banking_tariffs" in catalog:
            bundle["product"]["conditional_offers"]["banking_tariffs"] = catalog["banking_tariffs"]
        for name in ("cashback_rules", "new_client_offer", "pensioner_offer", "extra_cashback_mechanics"):
            if name in catalog:
                bundle["product"]["conditional_offers"][name] = catalog[name]
        bundle["product"]["supplementary_catalog_version"] = catalog["version"]
    validate_bundle(bundle)
    return deepcopy(bundle)

