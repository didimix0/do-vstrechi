"""Уточнения по уже затронутым темам. Здесь нет автоматической оценки достоверности."""
from copy import deepcopy
from .benefits import concrete_benefit_evidence
from .followups import followups_enabled
from .detail_policy import detail_policy_enabled, allowed_question, meaningful_detail, try_defer


def product_questions_enabled(scenario: dict) -> bool:
    return scenario.get('dialogue_features', {}).get('product_questions') is True


def new_question_state() -> dict:
    return {'covered': [], 'coverage_evidence': [], 'active_id': None, 'records': []}


def _cover(state, topic, employee_turn, basis=None):
    if topic not in state['covered']:
        state['covered'].append(topic)
    entry = {'question_id': topic, 'turn_id': employee_turn}
    if basis:
        entry['answer_basis'] = deepcopy(basis)
    if entry not in state['coverage_evidence']:
        state['coverage_evidence'].append(entry)
    for record in state['records']:
        if record['question_id'] == topic and record['status'] in {'open', 'deferred', 'unresolved','meeting_detail'}:
            record['status'] = 'answer_detected'
            if employee_turn not in record['answer_turn_ids']:
                record['answer_turn_ids'].append(employee_turn)
            if basis:
                record['answer_basis'] = deepcopy(basis)
            if state['active_id'] == topic:
                state['active_id'] = None


def update_explanation(session: dict, codes: set, employee_turn: str) -> bool:
    """Сохраняет кандидатов объяснения и ответы; возвращает факт отложенного уточнения."""
    state = session.setdefault('product_question_state', new_question_state())
    definitions = session['materials']['scenario']['product_questions']
    employee_text=next((t['text'] for t in session['history'] if t['id']==employee_turn),'')
    for item in definitions:
        if not allowed_question(session,item):
            continue
        if item.get('answer_patterns'):
            explanatory=any(c.startswith('explain_') for c in codes) or 'connect_benefit' in codes
            answered=explanatory and meaningful_detail(item,employee_text)
        else:
            answered=bool(codes.intersection(item['answer_actions']))
        if answered:
            _cover(state, item['id'], employee_turn)
    basis = concrete_benefit_evidence(session, codes, employee_turn)
    if basis and any(q['id'] == 'benefit_relevance' for q in definitions):
        _cover(state, 'benefit_relevance', employee_turn, basis)
    if detail_policy_enabled(session['materials']['scenario']) and codes & {'defer_to_meeting','defer_product_detail'} and state['active_id']:
        active=next(q for q in definitions if q['id']==state['active_id'])
        record=next(r for r in state['records'] if r['question_id']==active['id'])
        return try_defer(session,active,record,employee_text,employee_turn)
    if followups_enabled(session['materials']['scenario']) and 'defer_to_meeting' in codes and state['active_id']:
        active = next(q for q in definitions if q['id']==state['active_id'])
        if active['trigger']=='client_follow_up':
            record = next(r for r in state['records'] if r['question_id']==active['id'])
            record.update(status='meeting_detail')
            record['answer_turn_ids'].append(employee_turn)
            state['active_id'] = None
    deferred = False
    if 'defer_product_detail' in codes and state['active_id']:
        record = next(r for r in state['records'] if r['question_id'] == state['active_id'])
        record['status'] = 'deferred'
        record['answer_turn_ids'].append(employee_turn)
        state['active_id'] = None
        deferred = True
    return deferred


def next_product_question(session: dict, client_turn: str) -> dict | None:
    state = session['product_question_state']
    flags = session['flags']
    deferred = {r['question_id'] for r in state['records'] if r['status'] in {'deferred', 'unresolved', 'meeting_detail'}}
    for item in session['materials']['scenario']['product_questions']:
        if not allowed_question(session,item):
            continue
        if state['active_id'] and item['id'] != state['active_id']:
            continue
        if item['id'] in state['covered'] or item['id'] in deferred:
            continue
        if item['trigger']=='client_advanced_follow_up':
            advanced_ids={q['id'] for q in session['materials']['scenario']['product_questions'] if q.get('difficulty')=='advanced'}
            asked={r['question_id'] for r in state['records'] if r['question_id'] in advanced_ids and r['asked_turn_ids']}
            eligible=(state.get('followups_activated',False) and set(item.get('requires_covered',[]))<=set(state['covered'])
                      and (item['id'] in asked or len(asked)<2))
        elif item['trigger']=='client_follow_up':
            if not followups_enabled(session['materials']['scenario']):
                continue
            optional_ids = {q['id'] for q in session['materials']['scenario']['product_questions'] if q['trigger']=='client_follow_up'}
            asked = {r['question_id'] for r in state['records'] if r['question_id'] in optional_ids and r['asked_turn_ids']}
            eligible = state.get('followups_activated', False) and (item['id'] in asked or len(asked) < 2)
        else:
            eligible = (flags['service_discussed'] or flags['cashback_discussed']) if item['trigger'] == 'any_explanation' else flags[item['trigger']]
        if not eligible:
            continue
        state['active_id'] = item['id']
        record = next((r for r in state['records'] if r['question_id'] == item['id']), None)
        if record is None:
            record = {'question_id': item['id'], 'question_meaning': item['text'],
                      'fact_ids': deepcopy(item['fact_ids']), 'criterion_ids': deepcopy(item['criterion_ids']),
                      'status': 'open', 'asked_turn_ids': [], 'answer_turn_ids': []}
            state['records'].append(record)
        record['asked_turn_ids'].append(client_turn)
        return item
    return None


def explanation_review(session: dict) -> dict:
    enabled = product_questions_enabled(session['materials']['scenario'])
    state = session.get('product_question_state', new_question_state())
    history = {turn['id']: turn for turn in session['history']}
    records = deepcopy(state['records'])
    for record in records:
        record['client_quotes'] = [history[tid]['text'] for tid in record['asked_turn_ids'] if tid in history]
        record['employee_quotes'] = [history[tid]['text'] for tid in record['answer_turn_ids'] if tid in history]
        record['unanswered_employee_quotes'] = [history[tid]['text'] for tid in record.get('unanswered_turn_ids', []) if tid in history]
    return {'enabled': enabled, 'covered_topic_candidates': deepcopy(state['covered']),
            'coverage_evidence': deepcopy(state['coverage_evidence']), 'questions': records,
            'verification': 'requires_review',
            'notice': 'Обнаруженный ответ не подтверждает его достоверность. Отложенный ответ требует проверки; итоговый зачёт не выставлен.'}
