from dataclasses import dataclass
import re
from .grading import MONEY, NUMBER, benefit_detail_issue
from .followups import has_topic_detail
from .auto_review import has_closing_words


ACTION_DESCRIPTIONS = {
    "introduce": "Представился, назвал банк и обозначил повод звонка",
    "offer_brief_explanation": "Предлагает кратко рассказать об условиях и пользе карты",
    "ask_reason": "Выясняет причину отказа",
    "ask_needs": "Уточняет потребности или покупки",
    "explain_service": "Объясняет условия обслуживания",
    "explain_cashback": "Объясняет условия кешбэка",
    "explain_service_scope": "Различает бесплатные выпуск и обслуживание карты и отдельные платные услуги; это объяснение состава стоимости, а не слово бесплатно",
    "connect_benefit": "Связывает конкретное свойство карты с причиной сомнения или потребностью клиента; объясняет, зачем ему рассмотреть эту карту",
    "explain_cashback_mechanics": "Объясняет выбор категорий каждый месяц и начисление за подходящие покупки в выбранных категориях",
    "explain_cashback_limits": "Называет месячный предел начисления кешбэка и его период; это предел, а не гарантированная сумма",
    "explain_transfers": "Кратко объясняет переводы через СБП: способ, комиссию, конкретный объём и период",
    "explain_withdrawals": "Кратко объясняет снятие наличных: сеть банкоматов, комиссию или бесплатный объём с периодом",
    "explain_current_detail": "Содержательно отвечает на текущий конкретный вопрос об условиях: даёт значения и область применения либо объясняет различие двух правил. Простое да, выгодно, не знаю и обещание уточнить ответом не являются",
    "defer_to_meeting": "Предлагает разобрать дополнительные подробности лично на встрече, не выдумывая условия и не обещая все услуги без ограничений",
    "defer_product_detail": "Честно признаёт, что не знает ответа на заданный вопрос, в том числе короткое не знаю/не помню/не могу сказать; может предложить уточнение. Это не ответ и не подтверждение значения",
    "clarify_product_terms": "Явно уточняет или исправляет ранее спорное условие с опорой на паспорт продукта",
    "propose_next_step": "Предлагает сохранить или согласовать назначенную встречу",
    "confirm_time": "Явно подтверждает конкретное время из учебной карточки встречи",
    "confirm_address": "Явно подтверждает конкретный адрес из учебной карточки встречи",
    "ask_passport_availability": "Спрашивает, есть ли у клиента оригинал паспорта или будет ли паспорт при себе на встрече; это вопрос, а не простое напоминание",
    "remind_passport": "Просит подготовить или взять паспорт без вопроса о его наличии; не заменяет ask_passport_availability",
    "respect_refusal": "Принимает отказ и корректно завершает звонок",
    "possible_product_error": "Возможное собственное неверное обещание, требуется проверка",
    "pressure": "Давит на клиента или продолжает навязывать после окончательного отказа",
    "end_meeting": "Явно завершает упражнение или звонок",
    "unclear": "Действие неясно или не относится к сценарию",
}


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class ActionEvidence:
    code: str
    quote: str


def validate_analysis(payload: dict, employee_text: str) -> list[ActionEvidence]:
    if not isinstance(payload, dict) or set(payload) != {"actions"}:
        raise ContractError("Ожидается только поле actions.")
    actions = payload["actions"]
    if not isinstance(actions, list) or not 1 <= len(actions) <= 8:
        raise ContractError("Нужно от 1 до 8 действий.")
    result = []
    for item in actions:
        if not isinstance(item, dict) or set(item) != {"code", "quote"}:
            raise ContractError("Действие должно содержать только code и quote.")
        if not isinstance(item["code"], str) or item["code"] not in ACTION_DESCRIPTIONS:
            raise ContractError("Неизвестный код действия.")
        quote = item["quote"]
        if not isinstance(quote, str) or not quote.strip() or quote not in employee_text:
            raise ContractError("Подтверждающая цитата отсутствует в текущей реплике.")
        value = ActionEvidence(item["code"], quote)
        if value not in result:
            result.append(value)
    return result


def analysis_schema() -> dict:
    return {
        "type": "object",
        "properties": {"actions": {
            "type": "array", "minItems": 1, "maxItems": 8,
            "items": {"type": "object", "properties": {
                "code": {"type": "string", "enum": list(ACTION_DESCRIPTIONS)},
                "quote": {"type": "string", "minLength": 1},
            }, "required": ["code", "quote"], "additionalProperties": False},
        }},
        "required": ["actions"], "additionalProperties": False,
    }


def action_selection_schema() -> dict:
    """Модель выбирает смысл; исходный текст в свидетельство подставляет сервер."""
    return {"type": "object", "properties": {"actions": {
        "type": "array", "minItems": 1, "maxItems": 8,
        "items": {"type": "object", "properties": {
            "code": {"type": "string", "enum": list(ACTION_DESCRIPTIONS)}},
            "required": ["code"], "additionalProperties": False}}},
        "required": ["actions"], "additionalProperties": False}


def bind_action_evidence(payload: dict, employee_text: str, *, current_question_id=None) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"actions"} or not isinstance(payload["actions"], list):
        raise ContractError("Ожидается список действий текущей реплики.")
    if any(not isinstance(item, dict) or set(item) != {"code"} for item in payload["actions"]):
        raise ContractError("Модель должна вернуть только коды действий, без выдуманных цитат.")
    analysis = {"actions": [{"code": item["code"], "quote": employee_text} for item in payload["actions"]]}
    validate_analysis(analysis, employee_text)
    if not current_question_id:
        for action in analysis['actions']:
            if action['code']=='explain_current_detail':action['code']='unclear'
    # Узкий явный вопрос о сохранении встречи: не теряем его за фразой
    # «чтобы подробнее посмотреть условия». Вступления, отрицания и цитаты не подходят.
    meeting_question = re.match(r'^\s*(?:сохраняем|сохраним|оставляем)\s+(?:(?:нашу|назначенную)\s+)?встречу\b', employee_text, re.I)
    if meeting_question and '?' in employee_text and not any(a['code']=='propose_next_step' for a in analysis['actions']):
        analysis['actions']=[a for a in analysis['actions'] if a['code']!='unclear']
        analysis['actions'].append({'code':'propose_next_step','quote':employee_text})
    for action in analysis['actions']:
        if action['code'] in {'explain_transfers','explain_withdrawals'} and not has_topic_detail(action['code'], employee_text):
            action['code'] = 'explain_service'
        if action['code']=='defer_to_meeting' and not re.search(r'встреч|лично|приед', employee_text, re.I):
            action['code'] = 'unclear'
        if action['code']=='respect_refusal' and not has_closing_words(employee_text):
            action['code'] = 'unclear'
    # Общее действие анализатора тоже может содержать ответ на заданную тему.
    expected_topic = {'transfers':'explain_transfers','cash_withdrawal':'explain_withdrawals'}.get(current_question_id)
    if expected_topic and any(a['code']=='explain_service' for a in analysis['actions']) and has_topic_detail(expected_topic, employee_text):
        analysis['actions'].append({'code':expected_topic,'quote':employee_text})
    # Узкая проверка формы: простая команда подготовить документ не является вопросом.
    # Она не заменяет смысловую оценку отрицаний, цитат и контекста моделью/проверяющим.
    reminder = re.search(r"\b(?:подготовьте|приготовьте|возьмите|захватите|принесите|не забудьте)\b.{0,100}\bпаспорт", employee_text, re.I)
    question_form = re.search(r"\?|\b(?:есть|будет|имеется)\s+ли\b|\b(?:у вас|у тебя).{0,50}\bпаспорт|\bпаспорт.{0,50}\b(?:у вас|у тебя)|\b(?:возьм[её]те|сможете|получится)\b", employee_text, re.I)
    if reminder and not question_form:
        for action in analysis["actions"]:
            if action["code"] == "ask_passport_availability":
                action["code"] = "remind_passport"
        analysis["actions"] = [dict(items) for items in dict.fromkeys(tuple(a.items()) for a in analysis["actions"])]
    # Обычный заголовок о бесплатной карте требует уточнения состава услуг, а не обвинения.
    plain_free_card = re.fullmatch(r"(?:(?:наша|эта|дебетовая)\s+)?карта(?:\s+втб)?\s*[-—:]?\s*бесплатн(?:ая|а)[.!]?", employee_text.strip(), re.I)
    if plain_free_card:
        analysis["actions"] = [a for a in analysis["actions"] if a["code"] != "possible_product_error"]
        if not any(a["code"] == "explain_service" for a in analysis["actions"]):
            analysis["actions"].append({"code": "explain_service", "quote": employee_text})
    paid_service_mention = re.search(r"(?:дополнительн|отдельн|подключа|платн).{0,35}(?:услуг|сервис)|уведомлен|оповещ|\bсмс\b|\bsms\b|подписк", employee_text, re.I)
    if not paid_service_mention:
        for action in analysis["actions"]:
            if action["code"] == "explain_service_scope":
                action["code"] = "explain_service"
    # Одно лишь указание канала не объясняет ни одного условия карты.
    bare_pointer = re.fullmatch(
        r"(?:(?:посмотрите|смотрите|почитайте|читайте)\s+)?(?:на\s+(?:официальном\s+)?сайте(?:\s+(?:банка|втб))?|в\s+приложении(?:\s+(?:банка|втб(?:\s+онлайн)?))?)[.!]?",
        employee_text.strip(), re.I)
    if bare_pointer:
        explanations = {"explain_service", "explain_service_scope", "explain_cashback", "explain_cashback_mechanics",
                        "explain_cashback_limits", "connect_benefit", "clarify_product_terms"}
        analysis["actions"] = [a for a in analysis["actions"] if a["code"] not in explanations]
        if not analysis["actions"]:
            analysis["actions"] = [{"code": "unclear", "quote": employee_text}]
    # Название условия не является его раскрытием. Это проверка минимальной
    # конкретики; правильность значения и смысловую связь проверяем отдельно.
    has_monthly_amount = bool(MONEY.search(employee_text) and re.search(r"месяц|ежемесяч", employee_text, re.I))
    if current_question_id == "cashback_limit":
        # В ответ на «какой месячный предел кешбэка?» период уже задан вопросом.
        # Короткое «три тысячи рублей» не заставляет повторять ту же формулировку.
        plain_amount = re.fullmatch(r"(?:до\s+|максимум\s+|не более\s+)?" + NUMBER + r"(?:\s+" + NUMBER + r"){0,3}(?:\s*(?:₽|руб\w*))?(?:\s+в\s+месяц)?[.!]?", employee_text.strip(), re.I)
        has_monthly_amount = bool(has_monthly_amount or MONEY.search(employee_text) or plain_amount)
    for action in analysis["actions"]:
        if action["code"] == "explain_cashback_limits" and not has_monthly_amount:
            action["code"] = "explain_cashback"
        if action["code"] == "connect_benefit" and benefit_detail_issue(employee_text):
            action["code"] = "explain_cashback" if re.search(r"к[еэ]шб[еэ]к", employee_text, re.I) else "unclear"
    analysis["actions"] = [dict(items) for items in dict.fromkeys(tuple(a.items()) for a in analysis["actions"])]
    return analysis

