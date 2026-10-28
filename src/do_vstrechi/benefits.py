"""Конкретный ответ о свойствах карты не равен зачёту персонализации в разборе."""
import re
from .grading import CASHBACK, MONEY, RATE


CASHBACK_ACTIONS = {'explain_cashback', 'explain_cashback_mechanics', 'explain_cashback_limits'}


def grounded_benefit_dialogue(scenario):
    return scenario.get('dialogue_features', {}).get('grounded_benefit_dialogue') is True


def concrete_benefit_evidence(session, codes, employee_turn):
    """Сохраняет только реально сказанное. Значения сверяет отдельный оценщик.

    Смысл темы уже определён анализатором; здесь проверяется минимальная
    конкретность. История берётся лишь из продуктовых реплик сотрудника.
    Личная причина, покупки и сравнительная экономия не додумываются.
    """
    if not grounded_benefit_dialogue(session['materials']['scenario']):
        return None
    if codes & {'possible_product_error', 'pressure'} or session['flags']['pending_product_clarification']:
        return None
    current = next(t['text'] for t in session['history'] if t['id'] == employee_turn)
    if codes & CASHBACK_ACTIONS and CASHBACK.search(current):
        rows = []
        for turn in session['history']:
            if turn['role'] != 'employee':
                continue
            actions = {o['code'] for o in session['observations'] if o['turn_id'] == turn['id']}
            if actions & CASHBACK_ACTIONS and 'possible_product_error' not in actions:
                rows.append(turn)
        scope = '\n'.join(t['text'] for t in rows)
        # Процент в связанной с кешбэком фразе или сумма его начисления.
        clauses = re.split(r'[!?;\n]|(?<!\d)\.(?!\d)', current)
        quantified = any(CASHBACK.search(c) and (RATE.search(c) or MONEY.search(c)) for c in clauses)
        if not quantified and RATE.search(current) and codes & {'explain_cashback_mechanics'}:
            quantified = True
        applicable = re.search(r'категори|подходящ\w*\s+покуп|выбран\w*\s+покуп|партн[её]р', scope, re.I)
        if quantified and applicable:
            return {'kind': 'concrete_product_conditions', 'topic': 'cashback',
                    'turn_ids': [t['id'] for t in rows]}
    if 'explain_service' in codes:
        transfer = (re.search(r'перевод', current, re.I) and re.search(r'СБП', current, re.I)
                    and re.search(r'без\s+комисси', current, re.I) and MONEY.search(current)
                    and re.search(r'месяц|ежемесяч', current, re.I))
        payment = (re.search(r'коммунальн|мобильн\w*\s+связ', current, re.I)
                   and re.search(r'без\s+комисси', current, re.I)
                   and re.search(r'ВТБ\s*Онлайн', current, re.I))
        if transfer or payment:
            return {'kind': 'concrete_product_conditions', 'topic': 'transfers_or_payments', 'turn_ids': [employee_turn]}
    return None
