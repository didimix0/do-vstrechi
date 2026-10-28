from copy import deepcopy
import sys
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from do_vstrechi.data import load_bundle, fingerprint
from do_vstrechi.engine import advance,new_session,review_packet
from do_vstrechi.evaluation import apply_evaluation,finalize_evaluation,evaluation_context
from do_vstrechi.contracts import ContractError,bind_action_evidence
from do_vstrechi.webapp import OPTIONS
from test_evaluation import completed_session,valid_payload


def say(s,key):
    text,codes=OPTIONS[key]
    text=text.format(**s['materials']['scenario']['appointment'])
    return advance(s,text,{'actions':[{'code':c,'quote':text} for c in codes]},mode='test')

def short_call():
    s=new_session(load_bundle())
    for key in ('intro','offer','service','benefit'): s=say(s,key)
    return s

def auto_session():
    s=completed_session()
    s['materials']['scenario']['dialogue_features']['automatic_review']=True
    s['material_hashes']['scenario']=fingerprint(s['materials']['scenario'])
    s['history'][1]['reaction']='objection'
    s['history'][-1]['reaction']='passport_appointment'
    return s

class SequenceProvider:
    def __init__(self,*payloads): self.payloads=payloads; self.seen=[]; self.metrics={}
    def evaluate_session(self,session):
        self.seen.append(deepcopy(session))
        self.metrics={'status':'ok','role':'session_evaluation','usage':{'total_tokens':1}}
        return deepcopy(self.payloads[min(len(self.seen)-1,len(self.payloads)-1)])


class PhoneQuestionsTests(unittest.TestCase):
    def test_short_call_asks_two_missing_topics_then_resumes_meeting_offer(self):
        s=say(short_call(),'meeting')
        self.assertEqual(s['history'][-1]['product_question_id'],'transfers')
        s=say(s,'transfers')
        self.assertEqual(s['history'][-1]['product_question_id'],'cash_withdrawal')
        s=say(s,'withdrawals')
        self.assertEqual(s['history'][-1]['reaction'],'ask_time')
        s=say(s,'both');s=say(s,'passport')
        self.assertEqual(s['end_reason'],'appointment_confirmed')
        self.assertTrue(next(c for c in review_packet(s)['criteria'] if c['id']=='C07')['hidden'])

    def test_previously_explained_topics_are_not_asked_again(self):
        s=short_call();s=say(s,'transfers');s=say(s,'withdrawals');s=say(s,'meeting')
        self.assertEqual(s['history'][-1]['reaction'],'ask_time')
        self.assertFalse(any(r['question_id'] in {'transfers','cash_withdrawal'} for r in s['product_question_state']['records']))

    def test_saved_earlier_policy_allows_two_followup_deferrals(self):
        s=short_call();s['materials']['scenario']['dialogue_features']['distinct_client_behavior']=False
        s=say(s,'meeting');s=say(s,'meeting_details')
        self.assertEqual(s['history'][-1]['product_question_id'],'cash_withdrawal')
        s=say(s,'meeting_details')
        self.assertEqual(s['history'][-1]['reaction'],'ask_time')
        records=[r for r in s['product_question_state']['records'] if r['question_id'] in {'transfers','cash_withdrawal'}]
        self.assertTrue(all(r['status']=='meeting_detail' for r in records))
        self.assertNotIn('transfers',s['product_question_state']['covered'])

    def test_website_pointer_and_empty_topic_do_not_count_as_answer(self):
        for code,text in [('explain_transfers','Переводы у нас хорошие.'),('explain_withdrawals','В любых банкоматах удобно.'),('defer_to_meeting','На сайте.')]:
            payload=bind_action_evidence({'actions':[{'code':code}]},text)
            self.assertNotIn(code,[a['code'] for a in payload['actions']])
        wrong=bind_action_evidence({'actions':[{'code':'respect_refusal'}]},'Все услуги бесплатные, уведомления тоже. Никаких лимитов нет.')
        self.assertNotIn('respect_refusal',[a['code'] for a in wrong['actions']])

    def test_explicit_final_refusal_is_not_delayed_by_optional_questions(self):
        s=short_call();s['ending_preference']='decline';s=say(s,'meeting')
        self.assertEqual(s['history'][-1]['reaction'],'decline')
        self.assertFalse(any(r['question_id']=='transfers' for r in s['product_question_state']['records']))

    def test_generic_service_classifier_can_still_answer_the_current_transfer_question(self):
        text=OPTIONS['transfers'][0]
        analysis=bind_action_evidence({'actions':[{'code':'explain_service'}]},text,current_question_id='transfers')
        self.assertIn('explain_transfers',[a['code'] for a in analysis['actions']])

    def test_conditions_explained_in_the_introduction_are_remembered(self):
        text=OPTIONS['intro'][0]+' '+OPTIONS['transfers'][0]
        s=advance(new_session(load_bundle()),text,{'actions':[{'code':code,'quote':text} for code in ('introduce','explain_transfers')]},mode='test')
        self.assertTrue(s['flags']['service_discussed'])
        self.assertIn('transfers',s['product_question_state']['covered'])

    def test_refusal_criterion_is_visible_when_it_really_applies(self):
        s=short_call();s['ending_preference']='decline';s=say(s,'meeting');s=say(s,'pressure');s=say(s,'close')
        c=next(c for c in review_packet(s)['criteria'] if c['id']=='C07')
        self.assertFalse(c.get('hidden',False))


class AutomaticReviewTests(unittest.TestCase):
    def test_unknown_own_promise_gets_specific_negative_feedback_without_pending_verdict(self):
        s=auto_session();p=valid_payload(s)
        p['claims'][0].update(status='not_established',facts=[],explanation='Условия другого банка неизвестны, превосходство не подтверждено.')
        result=finalize_evaluation(p,s)
        self.assertEqual(result['training_verdict'],'retry')
        for code in ('C04','C05'):
            c=next(c for c in result['criteria'] if c['id']==code)
            self.assertEqual(c['status'],'not_met')
            self.assertIn('не подтверждается',c['reason'])
        self.assertFalse(result['rating']['provisional'])

    def test_disagreement_is_resolved_by_automatic_second_pass(self):
        s=auto_session();p=valid_payload(s)
        first=deepcopy(p);next(c for c in first['criteria'] if c['id']=='C03').update(status='requires_review',reason='Недостаточно выбранных цитат.')
        provider=SequenceProvider(first,p)
        result=apply_evaluation(s,provider)
        self.assertEqual(result['training_verdict'],'passed')
        self.assertEqual(result['evaluation']['automatic_verification_attempts'],2)
        self.assertEqual(len(provider.seen),2)
        self.assertIn('_automatic_review_feedback',provider.seen[1])
        self.assertEqual(len(result['calls']),2)
        self.assertNotIn('_automatic_review_feedback',result)

    def test_technical_nonresolution_never_invents_a_training_grade(self):
        s=auto_session();p=valid_payload(s)
        next(c for c in p['criteria'] if c['id']=='C03').update(status='requires_review',reason='Не согласованы вывод и цитаты.')
        original=deepcopy(s);provider=SequenceProvider(p)
        with self.assertRaises(ContractError):apply_evaluation(s,provider)
        self.assertEqual(len(provider.seen),2)
        self.assertEqual(s,original)
        self.assertNotIn('evaluation',s)

    def test_missing_proof_is_rechecked_instead_of_awarding_credit(self):
        s=auto_session();p=valid_payload(s);first=deepcopy(p)
        next(c for c in first['criteria'] if c['id']=='C03')['evidence']=[]
        result=apply_evaluation(s,SequenceProvider(first,p))
        self.assertEqual(result['evaluation']['automatic_verification_attempts'],2)

    def test_absent_final_refusal_is_hidden_and_has_no_weight(self):
        s=auto_session();p=valid_payload(s)
        next(c for c in p['criteria'] if c['id']=='C07').update(status='not_met',reason='Отказа не было.')
        s=apply_evaluation(s,SequenceProvider(p));packet=review_packet(s)
        c=next(c for c in packet['criteria'] if c['id']=='C07')
        self.assertTrue(c['hidden']);self.assertEqual(c['status'],'not_applicable')
        self.assertEqual(packet['rating']['value'],100)
        self.assertEqual(packet['rating']['applicable'],9)

    def test_context_requests_brief_call_and_automatic_evidence_based_decisions(self):
        context=evaluation_context(auto_session())
        self.assertTrue(context['automatic_review_policy']['phone_call_only'])

    def test_invalid_reference_is_automatically_requested_again(self):
        s=auto_session();p=valid_payload(s);first=deepcopy(p)
        first['criteria'][0]['evidence']=[{'turn_id':'invented-turn'}]
        provider=SequenceProvider(first,p)
        result=apply_evaluation(s,provider)
        self.assertEqual(result['training_verdict'],'passed')
        self.assertEqual(len(provider.seen),2)
        self.assertEqual(result['calls'][0]['status'],'error')

    def test_absence_of_bad_promises_uses_the_checked_claims_instead_of_an_impossible_absence_quote(self):
        s=auto_session();p=valid_payload(s)
        next(c for c in p['criteria'] if c['id']=='C05')['evidence']=[]
        provider=SequenceProvider(p)
        result=apply_evaluation(s,provider)
        self.assertEqual(result['training_verdict'],'passed')
        self.assertEqual(len(provider.seen),1)
        self.assertTrue(next(c for c in result['evaluation']['criteria'] if c['id']=='C05')['evidence'])

    def test_sales_pitch_after_final_refusal_is_a_missing_closing_not_an_inapplicable_step(self):
        s=auto_session();s['client_outcome']='declined';s['end_reason']='client_declined'
        s['history'][-1].update(text='До свидания.',reaction='closed')
        s['history'].insert(2,{'id':'final-no','role':'client','text':'Нет, карту получать не буду, больше обсуждать не хочу.','reaction':'firm_refusal'})
        p=valid_payload(s)
        next(c for c in p['criteria'] if c['id']=='C06').update(status='not_applicable',evidence=[])
        next(c for c in p['criteria'] if c['id']=='C07').update(status='met',reason='Решение клиента уважено.',evidence=[{'turn_id':'e2'}])
        result=finalize_evaluation(p,s)
        self.assertEqual(next(c for c in result['criteria'] if c['id']=='C06')['status'],'not_met')
        self.assertEqual(next(c for c in result['criteria'] if c['id']=='C07')['status'],'not_met')
        self.assertEqual(result['training_verdict'],'retry')


if __name__=='__main__':unittest.main()
