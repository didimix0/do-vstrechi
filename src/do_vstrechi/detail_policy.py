"""Углублённые вопросы и один честно отложенный ответ, без изменения фактов."""
import re
from .grading import RATE,MONEY,NUMBER


def detail_policy_enabled(scenario):
    return scenario.get('dialogue_features',{}).get('distinct_client_behavior') is True


def allowed_question(session, question):
    if question.get('behavior_only') and not detail_policy_enabled(session['materials']['scenario']):
        return False
    return not question.get('profiles') or session.get('client_profile',{}).get('id') in question['profiles']


def meaningful_detail(question, text):
    patterns=question.get('answer_patterns',[])
    return bool(patterns) and all(re.search(pattern,text,re.I) for pattern in patterns)


def promised_clarification(text):
    # Нужны собственное обязательство проверить и обсуждение именно на встрече.
    # Вопрос, просьба проверить самому и отказ от проверки обязательством не являются.
    if re.search(r'не\s+(?:буду\s+)?(?:уточн|провер|расскаж|объясн)|не\s+могу\s+(?:обещать|проверить|уточнить)|(?:вы|сами)\s+(?:уточните|проверьте|посмотрите)|[«»"]',text,re.I):
        return False
    own_check=re.search(r'\b(?:уточню|уточним|проверю|проверим|узнаю|выясню|перепроверю|сверю|смогу\s+(?:уточнить|проверить))\b',text,re.I)
    meeting=re.search(r'(?:на|при|к|во\s+время)\s+(?:нашей\s+)?встреч[еуи]',text,re.I)
    explanation=re.search(r'расскаж|объясн|разбер[её]м|покаж|посмотрим|верн[её]мся',text,re.I)
    return bool(own_check and meeting and explanation)


def try_defer(session, question, record, text, employee_turn):
    state=session['product_question_state']
    accepted=(question.get('difficulty')=='advanced' and question.get('defer_allowed') is True
              and state.get('accepted_deferrals',0)<1 and promised_clarification(text))
    if accepted:
        state['accepted_deferrals']=state.get('accepted_deferrals',0)+1
        record.update(status='meeting_detail',deferral_policy_accepted=True,
                      deferral_reason='Один сложный вопрос: сотрудник обязался уточнить и объяснить на встрече.')
        if employee_turn not in record['answer_turn_ids']:record['answer_turn_ids'].append(employee_turn)
        state['active_id']=None
    else:
        reason=('basic_condition' if question.get('difficulty')!='advanced' else
                'quota_exhausted' if state.get('accepted_deferrals',0)>=1 else 'no_clear_commitment')
        record.setdefault('rejected_deferrals',[]).append({'turn_id':employee_turn,'reason':reason})
    return accepted


def deferral_context(session):
    definitions={q['id']:q for q in session['materials']['scenario'].get('product_questions',[])}
    records=session.get('product_question_state',{}).get('records',[])
    return {'maximum_accepted':1,'accepted':session.get('product_question_state',{}).get('accepted_deferrals',0),
            'rule':'Только один углублённый вопрос можно отложить с обещанием уточнить и объяснить на встрече. Бесплатность карты, ставка/лимит кешбэка, число категорий и базовые условия переводов и снятия требуют ответа в звонке.',
            'questions':[{'id':r['question_id'],'difficulty':definitions.get(r['question_id'],{}).get('difficulty','basic'),
                          'status':r['status'],'accepted':r.get('deferral_policy_accepted',False),
                          'employee_turn_ids':list(r['answer_turn_ids'])} for r in records]}


def reconcile_deferrals(result, session, by_id, review):
    if not detail_policy_enabled(session['materials']['scenario']):
        return
    definitions={q['id']:q for q in session['materials']['scenario']['product_questions']}
    history={t['id']:t for t in session['history']}
    accepted_ids=set()
    for record in session.get('product_question_state',{}).get('records',[]):
        if record.get('deferral_policy_accepted'):
            accepted_ids.update(tid for tid in record['answer_turn_ids'] if tid in history and pure_process_promise(history[tid]['text']))
        if (definitions[record['question_id']].get('difficulty','basic')=='basic' and record.get('rejected_deferrals')
                and record['status']!='answer_detected'):
            evidence=[{'turn_id':r['turn_id'],'quote':history[r['turn_id']]['text']} for r in record['rejected_deferrals'] if r['turn_id'] in history]
            if evidence:
                for code in record['criterion_ids']:
                    if code in by_id:
                        by_id[code].update(status='not_met',reason='Клиент спросил о базовом условии, но сотрудник отложил ответ и так его не объяснил: '+record['question_meaning'],
                                          next_step='Дайте конкретный ответ на этот базовый вопрос в звонке.',evidence=evidence[:6])
    excluded=[c for c in result['claims'] if c['evidence']['turn_id'] in accepted_ids]
    if excluded:
        result['excluded_process_promises']=excluded
        result['claims']=[c for c in result['claims'] if c['evidence']['turn_id'] not in accepted_ids]
    for code in ('C04','C05'):
        c=by_id.get(code)
        if c and c['status']=='not_met' and c['evidence'] and {e['turn_id'] for e in c['evidence']}<=accepted_ids:
            if (result.get('coverage_complete') and result['claims']
                    and all(claim['status'] in {'supported','corrected'} for claim in result['claims'])):
                c.update(model_status='not_met',status='met',deferral_policy_guard=True,
                         reason='Проверенные продуктовые утверждения подтверждены материалами. Один сложный вопрос перенесён с обещанием уточнить и объяснить на встрече; это действие сотрудника, а не неподтверждённый тариф.',
                         next_step='',evidence=[claim['evidence'].copy() for claim in result['claims'][:6]])
            else:
                review(code,'Обещание уточнить один сложный вопрос к встрече не является тарифным утверждением. Перепроверьте остальные условия и их источники отдельно.')


def pure_process_promise(text):
    return (promised_clarification(text) and not RATE.search(text) and not MONEY.search(text)
            and not re.search(NUMBER+r'\s+(?:месяц|год|дн|категор)',text,re.I)
            and not re.search(r'\d|%|бесплат|без\s+комисси|нулев\w*\s+комисси|без\s+огранич|гарант|любые\s+покупки|все\s+услуги|навсегда',text,re.I))
