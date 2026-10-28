"""Короткие вопросы клиента по нераскрытым темам, без полного опроса по тарифу."""
import re
from .grading import MONEY, RATE


def followups_enabled(scenario):
    return scenario.get('dialogue_features', {}).get('client_followups') is True


def activate_followups(session, codes):
    if (followups_enabled(session['materials']['scenario']) and 'propose_next_step' in codes
            and session['ending_preference']=='appointment'
            and (session['flags']['service_discussed'] or session['flags']['cashback_discussed'])):
        session['product_question_state']['followups_activated'] = True
        session['product_question_state']['meeting_offer_pending'] = True


def has_topic_detail(code, text):
    if code == 'explain_transfers':
        return bool(re.search(r'перевод|СБП', text, re.I) and re.search(r'СБП', text, re.I)
                    and re.search(r'комисси|бесплат', text, re.I) and MONEY.search(text)
                    and re.search(r'месяц|ежемесяч', text, re.I))
    if code == 'explain_withdrawals':
        return bool(re.search(r'банкомат|налич|снят', text, re.I)
                    and re.search(r'ВТБ|друг\w*\s+банк', text, re.I)
                    and re.search(r'комисси|бесплат', text, re.I)
                    and (RATE.search(text) or (MONEY.search(text) and re.search(r'месяц|день|сутк', text, re.I))))
    return False
