from copy import deepcopy
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from do_vstrechi.data import load_bundle
from do_vstrechi.engine import new_session,advance,review_packet
from do_vstrechi.webapp import OPTIONS
from do_vstrechi.conversation import naturalize_client_reply
from do_vstrechi.detail_policy import promised_clarification,reconcile_deferrals,pure_process_promise
from do_vstrechi.evaluation import evaluation_context
from do_vstrechi.client import client_context,validate_client_reply


def say(s,key):
    text,codes=OPTIONS[key];text=text.format(**s['materials']['scenario']['appointment'])
    return raw(s,text,*codes)


def raw(s,text,*codes):
    return advance(s,text,{'actions':[{'code':c,'quote':text} for c in codes]},mode='test')


def prepared(character='skeptical'):
    s=new_session(load_bundle(),character=character)
    for key in ('intro','offer','service','benefit'):s=say(s,key)
    return s


def advanced():
    s=prepared()
    for key in ('meeting','transfers','withdrawals'):s=say(s,key)
    assert s['product_question_state']['active_id']=='withdrawal_limits'
    return s


class CharacterDepthTests(unittest.TestCase):
    def test_model_cannot_add_its_own_extra_time_reminder(self):
        s=say(say(new_session(load_bundle(),character='hurried'),'intro'),'offer');s['client_reply_mode']='natural'
        class ExtraReminder:
            metrics={}
            def generate_client_reply(self,session):return 'Хорошо, рассказывайте. Только быстро, времени немного.'
        s=naturalize_client_reply(s,ExtraReminder())
        self.assertEqual(s['history'][-1]['reply_source'],'scripted_character_style')
        self.assertNotIn('времени немного',s['history'][-1]['text'])
        self.assertEqual(s['character_state']['time_reminders'],1)
        self.assertEqual(s['client_reply_notice'],'')

    def test_accepted_process_promise_is_not_an_unverified_financial_claim(self):
        s=say(advanced(),'clarify_later');history={t['id']:t for t in s['history']}
        tid=next(r for r in s['product_question_state']['records'] if r.get('deferral_policy_accepted'))['answer_turn_ids'][0]
        evidence={'turn_id':tid,'quote':history[tid]['text']}
        result={'coverage_complete':True,'claims':[{'status':'supported','evidence':{'turn_id':'t03-employee','quote':history['t03-employee']['text']}},
                                                  {'status':'not_established','evidence':evidence.copy()}]}
        criteria={code:{'status':'not_met','evidence':[evidence.copy()]} for code in ('C04','C05')}
        reconcile_deferrals(result,s,criteria,lambda *args:None)
        self.assertEqual(len(result['excluded_process_promises']),1)
        self.assertTrue(all(c['status']=='met' for c in criteria.values()))
        self.assertTrue(pure_process_promise('Я уточню точный процент комиссии и подробно объясню на встрече.'))
        self.assertFalse(pure_process_promise('Все услуги бесплатные, я уточню и подробно объясню на встрече.'))
        self.assertFalse(pure_process_promise('Комиссия два процента, я уточню и подробно объясню на встрече.'))

    def test_natural_indirect_question_without_question_mark_is_accepted(self):
        s=say(say(say(new_session(load_bundle(),character='skeptical'),'intro'),'offer'),'service')
        text='Понял про стоимость. Но не совсем ясно, какую именно пользу принесёт вторая карта'
        self.assertEqual(validate_client_reply({'reaction':'product_question','text':text},client_context(s)),text)

    def test_hurried_two_different_reminders_without_changing_success(self):
        s=prepared('hurried')
        for key in ('meeting','transfers','withdrawals','both','passport'):s=say(s,key)
        cues=[t['character_cue']['text'] for t in s['history'] if t.get('character_cue')]
        self.assertEqual(len(cues),2);self.assertEqual(len(set(cues)),2)
        self.assertEqual(s['character_state']['time_reminders'],2)
        self.assertEqual(s['client_outcome'],'appointment_agreed')
        self.assertFalse(any(t.get('character_cue') for t in prepared('calm')['history']))

    def test_hurried_reacts_to_long_answers_but_not_forever_or_by_ending_call(self):
        s=say(say(new_session(load_bundle(),character='hurried'),'intro'),'offer')
        text=('Выпуск и обслуживание самой карты бесплатные. Уведомления подключаются отдельно за 129 рублей в месяц. ')*6
        for _ in range(4):s=raw(s,text,'explain_service_scope')
        long_cues=[t['character_cue'] for t in s['history'] if t.get('character_cue',{}).get('long_employee_answer')]
        self.assertEqual(len(long_cues),2)
        self.assertEqual(len({c['text'] for c in long_cues}),2)
        self.assertEqual(s['character_state']['time_reminders'],2)
        self.assertEqual(s['status'],'active')
        self.assertFalse(s['flags']['firm_refusal'])

    def test_time_cue_is_not_lost_or_repeated_by_client_generator(self):
        s=say(new_session(load_bundle(),character='hurried'),'intro');s['client_reply_mode']='natural'
        class NoCall:
            def generate_client_reply(self,session):raise AssertionError('Не нужен отдельный вызов для обязательной фразы')
        generated=naturalize_client_reply(s,NoCall())
        self.assertEqual(generated['history'][-1]['text'],s['history'][-1]['text'])
        self.assertEqual(generated['history'][-1]['reply_source'],'scripted_character_behavior')
        self.assertTrue(generated['history'][-1]['text'].startswith('Здравствуйте.'))

    def test_no_time_reminder_after_final_refusal(self):
        s=prepared('hurried');s=raw(s,'Вы должны получить карту.','pressure')
        self.assertNotIn('character_cue',s['history'][-1])
        self.assertEqual(s['history'][-1]['reaction'],'firm_refusal')

    def test_skeptic_adds_two_advanced_questions_and_accepts_one_deferred(self):
        s=advanced();s=say(s,'clarify_later')
        state=s['product_question_state']
        self.assertEqual(state['accepted_deferrals'],1)
        first=next(r for r in state['records'] if r['question_id']=='withdrawal_limits')
        self.assertEqual(first['status'],'meeting_detail');self.assertNotIn('withdrawal_limits',state['covered'])
        self.assertEqual(state['active_id'],'withdrawal_fee_base')
        s=say(s,'withdrawal_fee_base');s=say(s,'both');s=say(s,'passport')
        self.assertEqual(s['end_reason'],'appointment_confirmed')
        context=evaluation_context(s)
        self.assertEqual(context['detail_deferral_policy']['accepted'],1)
        self.assertEqual(sum(q['difficulty']=='advanced' for q in context['detail_deferral_policy']['questions']),2)

    def test_second_deferral_is_rejected_but_employee_can_correct_it(self):
        s=say(advanced(),'clarify_later');s=say(s,'clarify_later')
        self.assertEqual(s['product_question_state']['accepted_deferrals'],1)
        self.assertEqual(s['product_question_state']['active_id'],'withdrawal_fee_base')
        self.assertEqual(s['status'],'active')
        record=s['product_question_state']['records'][-1]
        self.assertEqual(record['rejected_deferrals'][-1]['reason'],'quota_exhausted')
        s=say(s,'withdrawal_fee_base');s=say(s,'both');s=say(s,'passport')
        self.assertEqual(s['end_reason'],'appointment_confirmed')

    def test_ignorance_or_vague_meeting_promise_does_not_spend_deferral(self):
        for text,codes in [('Не знаю.',['defer_product_detail']),('Расскажу на встрече.',['defer_to_meeting']),('Посмотрите сами на сайте.',['defer_product_detail'])]:
            s=raw(advanced(),text,*codes)
            self.assertEqual(s['product_question_state'].get('accepted_deferrals',0),0)
            self.assertEqual(s['product_question_state']['active_id'],'withdrawal_limits')
            s=say(s,'clarify_later')
            self.assertEqual(s['product_question_state']['accepted_deferrals'],1)
        for text in ('Я не буду уточнять и расскажу на встрече.','Вы проверьте, на встрече расскажете.','Он сказал «я уточню и объясню на встрече».'):
            self.assertFalse(promised_clarification(text))

    def test_basic_conditions_cannot_be_deferred_even_with_good_promise(self):
        for topic in ('service_scope','cashback_limit','cashback_rate','cashback_categories'):
            if topic=='service_scope':
                s=say(say(say(new_session(load_bundle(),character='skeptical'),'intro'),'offer'),'basic_service')
            else:
                s=say(prepared(),'cashback')
                if topic!='cashback_limit':s=say(s,'cashback_limit')
                if topic=='cashback_categories':s=say(s,'cashback_rate')
            self.assertEqual(s['product_question_state']['active_id'],topic)
            s=say(s,'clarify_later')
            self.assertEqual(s['product_question_state']['active_id'],topic)
            self.assertEqual(s['product_question_state'].get('accepted_deferrals',0),0)

    def test_vague_detail_answer_is_not_credited_and_correct_one_is(self):
        s=raw(advanced(),'Да, всё удобно.','explain_current_detail')
        self.assertEqual(s['product_question_state']['active_id'],'withdrawal_limits')
        s=say(s,'withdrawal_limits')
        self.assertIn('withdrawal_limits',s['product_question_state']['covered'])
        self.assertEqual(s['product_question_state']['active_id'],'withdrawal_fee_base')

    def test_unanswered_second_detail_cannot_sneak_into_automatic_meeting(self):
        s=say(advanced(),'clarify_later')
        for _ in range(2):s=say(s,'clarify_later')
        s=say(s,'both')
        self.assertEqual(s['status'],'ended');self.assertEqual(s['end_reason'],'client_unresolved_questions')

    def test_grading_does_not_accept_ignored_basic_or_treat_one_accepted_detail_as_fact(self):
        s=say(say(say(new_session(load_bundle()),'intro'),'offer'),'basic_service');s=say(s,'clarify_later')
        result={'claims':[]};criteria={'C04':{'status':'met','evidence':[]}}
        reconcile_deferrals(result,s,criteria,lambda *args:None)
        self.assertEqual(criteria['C04']['status'],'not_met')
        s=say(advanced(),'clarify_later');criteria={'C04':{'status':'met','evidence':[]}}
        reconcile_deferrals(result,s,criteria,lambda *args:None)
        self.assertEqual(criteria['C04']['status'],'met')
        first=next(r for r in s['product_question_state']['records'] if r.get('deferral_policy_accepted'))
        self.assertNotIn(first['question_id'],s['product_question_state']['covered'])

    def test_already_explained_advanced_detail_is_not_asked_again(self):
        s=prepared();s=say(s,'withdrawal_limits');s=say(s,'meeting');s=say(s,'transfers')
        self.assertEqual(s['product_question_state']['active_id'],'withdrawal_fee_base')
        self.assertFalse(any(r['question_id']=='withdrawal_limits' for r in s['product_question_state']['records']))
