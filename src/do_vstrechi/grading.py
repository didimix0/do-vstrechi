"""Прозрачный рейтинг и минимальные требования к объяснению количественной выгоды."""
import re


NUMBER = r"(?:\d+(?:[.,]\d+)?|нол\w*|одн\w*|один|дв[аеу]\w*|тр[иеё]\w*|четыр\w*|пят\w*|шест\w*|сем\w*|вос[еь]м\w*|девят\w*|десят\w*|одиннадцат\w*|двенадцат\w*|тринадцат\w*|четырнадцат\w*|пятнадцат\w*|двадцат\w*|тридцат\w*|сорок\w*|пятьдесят|сто|ста|сот\w*|тысяч\w*|миллион\w*)"
RATE = re.compile(NUMBER + r"(?:\s+" + NUMBER + r"){0,3}\s*(?:%|процент\w*)", re.I)
MONEY = re.compile(NUMBER + r"(?:\s+" + NUMBER + r"){0,3}\s*(?:₽|руб\w*)", re.I)
CASHBACK = re.compile(r"к[еэ]шб[еэ]к\w*", re.I)


def cashback_detail_issue(text):
    """Проверка полноты, а не правильности цифр: правильность сверяет оценщик с источником."""
    if not CASHBACK.search(text):
        return None
    # Числа времени/адреса, количество категорий и цена другой услуги не являются ставкой кешбэка.
    clauses = re.split(r"[!?;\n]|(?<!\d)\.(?!\d)", text)
    related = []
    for index, clause in enumerate(clauses):
        if CASHBACK.search(clause):
            related.append(clause)
            for following in clauses[index+1:index+5]:
                # Ставка и общий предел могут быть в следующих предложениях.
                # Цену уведомлений, лимит переводов или ставку вклада не заимствуем.
                if re.search(r'уведомлен|оповещ|перевод|СБП|банкомат|налич|снят|вклад|накопитель|обслужив|выпуск|достав',following,re.I):
                    break
                if re.search(r'категори|к[еэ]шб[еэ]к|общ\w*\s+(?:предел|лимит)',following,re.I) or re.match(r'\s*(?:до\b|максим\w*\b|лимит\b|предел\b|не более\b)',following,re.I):
                    related.append(following)
                elif following.strip():
                    break
    scope = " ".join(related)
    has_value = bool(RATE.search(scope) or (MONEY.search(scope) and re.search(r"месяц|ежемесяч|покупк|расход|начисл", scope, re.I)))
    if not has_value:
        return "Кешбэк назван без конкретной ставки или суммы и её применимости. Слов «много кешбэка» недостаточно для объяснения выгоды."
    if not re.search(r"категори\w*|подходящ\w*\s+покуп|партн[её]р\w*|акци\w*", text, re.I):
        return "Не объяснено, за какие покупки и при каких условиях начисляется названный кешбэк."
    if not (MONEY.search(scope) and re.search(r"месяц|ежемесяч", scope, re.I)):
        return "Не назван конкретный месячный предел кешбэка; одного процента недостаточно."
    return None


def benefit_detail_issue(text):
    """Не позволяет засчитать пустой заголовок; смысловую связь с сомнением оценивает LLM."""
    # Бесплатность — конкретное нулевое значение. Назван и состав стоимости.
    if re.search(r"бесплат\w*|без\s+плат\w*|(?:0|ноль|нулев\w*)\s*(?:₽|руб\w*|стоим\w*)", text, re.I) and re.search(r"выпуск|обслуживан|доставк", text, re.I):
        return None
    issue = cashback_detail_issue(text)
    if issue:
        return issue
    if CASHBACK.search(text):
        return None
    if re.search(r"(?:выгодн|больш|высок|низк|хорош)\w*\s+(?:ставк|процент|лимит|комисси)\w*", text, re.I) and not (RATE.search(text) or MONEY.search(text)):
        return "Количественная выгода описана общими словами без конкретного значения и условий."
    if re.fullmatch(r"[\W\w]{0,25}(?:карта\s+лучше|лучшая\s+карта|карта\s+бесплатн\w*|карта\s+выгодн\w*)[.!\s]*", text.strip(), re.I):
        return "Общая похвала или слово «бесплатная» без состава условий не объясняют пользу карты."
    return None


def quantitative_detail_issue(text):
    issue = cashback_detail_issue(text)
    if issue:
        return issue
    for clause in re.split(r"[!?;\n]|(?<!\d)\.(?!\d)", text):
        if (re.search(r"уведомлен|оповещ|\bсмс\b", clause, re.I)
                and re.search(r"платн|оплач|отдельн|тариф", clause, re.I)
                and not (MONEY.search(clause) and re.search(r"месяц|ежемесяч", clause, re.I))):
            return "Уведомления названы платными, но сумма и период оплаты не раскрыты. Нужно назвать применимый тариф или явно признать, что его требуется уточнить."
    return None


def completion_rating(criteria, *, provisional=False):
    counts = {status: sum(c["status"] == status for c in criteria)
              for status in ("met", "not_met", "requires_review", "not_applicable")}
    applicable = counts["met"] + counts["not_met"] + counts["requires_review"]
    # Округление половины вверх. Неприменимые критерии не дают ни бонуса, ни штрафа.
    value = (100 * counts["met"] * 2 + applicable) // (2 * applicable) if applicable else None
    return {"value": value, "maximum": 100, "applicable": applicable, **counts,
            "provisional": bool(provisional or counts["requires_review"]),
            "method": "Выполненные / применимые критерии × 100; равный вес. Спорные критерии баллов не дают.",
            "version": "equal-criteria-v1"}
