from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from do_vstrechi.client import client_context, validate_client_reply
from do_vstrechi.contracts import ContractError, bind_action_evidence
from do_vstrechi.conversation import naturalize_client_reply
from legacy_fixtures import load_bundle
from do_vstrechi.engine import advance, new_session, review_packet
from do_vstrechi.evaluation import evaluation_context, finalize_evaluation
from do_vstrechi.webapp import TrainerApp


def say(session, text, *codes):
    return advance(session, text, {'actions': [{'code': code, 'quote': text} for code in codes]}, mode='test')

def start():
    s = say(new_session(load_bundle()), 'Здравствуйте, я представитель ВТБ, звоню о встрече по карте.', 'introduce')
    return say(s, 'Выпуск и обслуживание бесплатны; подключаемые уведомления стоят 129 рублей в месяц.', 'explain_service_scope')

CASHBACK = 'Кешбэк рублями за подходящие покупки в выбранных категориях. Категории выбираются каждый месяц, ставка до 15 процентов в отдельных категориях.'
LIMIT = 'Общий предел кешбэка за подходящие покупки — три тысячи рублей в месяц, это максимум, а не гарантированная выплата.'


class BenefitDialogueTests(unittest.TestCase):
    def test_exact_reported_transcript_reaches_scheduling_without_loop_or_false_refusal(self):
        fixture = json.loads((ROOT / 'scripts/cases/reported_benefit_loop.v1.json').read_text(encoding='utf-8'))
        s = new_session(load_bundle())
        reactions = []
        for turn in fixture['turns']:
            s = say(s, turn['text'], *turn['recorded_actions'])
            reactions.append(s['history'][-1]['reaction'])
        self.assertEqual(reactions[4], 'product_question')
        self.assertEqual(next(t for t in s['history'] if t['id']=='t05-client')['product_question_id'], 'cashback_limit')
        self.assertEqual(reactions[-1], 'ask_time')
        self.assertEqual(s['status'], 'active')
        benefit = next(r for r in s['product_question_state']['records'] if r['question_id']=='benefit_relevance')
        self.assertEqual(benefit['status'], 'answer_detected')
        self.assertEqual(len(benefit['asked_turn_ids']), 1)
        self.assertEqual(benefit['answer_basis']['kind'], 'concrete_product_conditions')
        self.assertFalse(any(o['code']=='connect_benefit' for o in s['observations']))
        s = say(s, 'Сегодня в 18:00, город Учебный, улица Примерная, дом 10. Всё верно?', 'confirm_time', 'confirm_address')
        s = say(s, 'Оригинал паспорта РФ будет при вас?', 'ask_passport_availability')
        self.assertEqual(s['end_reason'], 'appointment_confirmed')
        self.assertEqual(review_packet(s)['training_verdict'], 'not_evaluated')
        groups = evaluation_context(s)['related_condition_turns']
        cashback = next(g for g in groups if g['topic']=='cashback')
        self.assertIn('t05-employee', cashback['turn_ids'])
        self.assertIn('t06-employee', cashback['turn_ids'])

    def test_concrete_benefit_then_specific_limit_remembers_separate_answers(self):
        s = say(start(), CASHBACK, 'explain_cashback_mechanics')
        self.assertEqual(s['history'][-1]['product_question_id'], 'cashback_limit')
        s = say(s, LIMIT, 'explain_cashback_limits')
        self.assertFalse(client_context(s)['conversation_memory']['open_questions'])
        self.assertEqual(s['history'][-1]['reaction'], 'detail_answered')
        basis = next(e['answer_basis'] for e in s['product_question_state']['coverage_evidence'] if e.get('answer_basis'))
        self.assertEqual(basis['topic'], 'cashback')

    def test_vague_praise_never_becomes_concrete_benefit_or_personalization(self):
        for text in ('Много кешбэка.', 'Наши условия выгодные.', 'Условия есть на сайте.'):
            s = say(start(), text, 'explain_cashback')
            self.assertNotIn('benefit_relevance', s['product_question_state']['covered'])
            self.assertEqual(s['history'][-1]['product_question_id'], 'benefit_relevance')
        guarded = bind_action_evidence({'actions':[{'code':'connect_benefit'}]}, 'Много кешбэка.')
        self.assertFalse(any(a['code']=='connect_benefit' for a in guarded['actions']))

    def test_service_price_and_meeting_numbers_do_not_count_as_cashback_value(self):
        s = say(start(), 'Есть кешбэк. Уведомления стоят 129 рублей в месяц. Встреча в 18:00.', 'explain_cashback')
        self.assertNotIn('benefit_relevance', s['product_question_state']['covered'])

    def test_concrete_payment_conditions_can_answer_general_benefit_question(self):
        for text in ('Переводы по номеру телефона через СБП без комиссии до 300 тысяч рублей в месяц в ВТБ Онлайн.',
                     'Оплата коммунальных услуг и мобильной связи в ВТБ Онлайн без комиссии.'):
            s = say(start(), text, 'explain_service')
            self.assertIn('benefit_relevance', s['product_question_state']['covered'])
            self.assertEqual(s['history'][-1]['reaction'], 'detail_answered')

    def test_an_unresolved_benefit_can_be_answered_later(self):
        s = start()
        s = say(s, 'Условия хорошие.', 'unclear')
        s = say(s, 'Просто карта выгодная.', 'unclear')
        self.assertEqual(s['history'][-1]['reaction'], 'unresolved_ack')
        s = say(s, CASHBACK + ' ' + LIMIT, 'explain_cashback_mechanics', 'explain_cashback_limits')
        self.assertEqual(s['product_question_state']['records'][0]['status'], 'answer_detected')
        s = say(s, 'Сохраняем встречу?', 'propose_next_step')
        self.assertEqual(s['history'][-1]['reaction'], 'ask_time')

    def test_known_false_promise_does_not_close_question(self):
        s = say(start(), 'Кешбэк 99 процентов на все покупки без ограничений.', 'explain_cashback', 'possible_product_error')
        self.assertEqual(s['history'][-1]['reaction'], 'source_question')
        self.assertNotIn('benefit_relevance', s['product_question_state']['covered'])

    def test_other_unanswered_topic_is_preserved_without_saying_everything_is_clear(self):
        s = say(new_session(load_bundle()), 'Представитель ВТБ, звоню о встрече.', 'introduce')
        s = say(s, 'Карта бесплатная.', 'explain_service')
        s = say(s, 'Не знаю про стоимость услуг.', 'defer_product_detail')
        s = say(s, CASHBACK + ' ' + LIMIT, 'explain_cashback_mechanics', 'explain_cashback_limits')
        self.assertEqual(s['history'][-1]['reaction'], 'partial_answer_ack')
        self.assertIn('предыдущим вопросом', s['history'][-1]['text'])
        with self.assertRaises(ContractError):
            validate_client_reply({'reaction':'partial_answer_ack','text':'Теперь всё понятно.'}, client_context(s))
        s = say(s, 'Сохраняем встречу?', 'propose_next_step')
        self.assertEqual(s['end_reason'], 'client_unresolved_questions')
        s['client_reply_mode']='natural'
        result = naturalize_client_reply(s, object())
        self.assertEqual(result['history'][-1]['reply_source'], 'scripted_ending')
        self.assertFalse(result.get('client_reply_notice'))

    def test_final_refusal_cannot_be_bypassed_by_detailed_benefits(self):
        s = say(start(), 'Вы обязаны получить карту.', 'pressure')
        s = say(s, CASHBACK + ' ' + LIMIT, 'explain_cashback_mechanics', 'explain_cashback_limits')
        self.assertEqual(s['history'][-1]['reaction'], 'firm_refusal')
        self.assertFalse(s['flags']['meeting_ready'])

    def test_previous_frozen_scenario_retains_old_answer_policy(self):
        bundle = load_bundle()
        bundle['scenario']['dialogue_features'].pop('grounded_benefit_dialogue')
        s = say(new_session(bundle), 'Представитель ВТБ, звоню о встрече.', 'introduce')
        s = say(s, 'Выпуск и обслуживание бесплатны; уведомления при подключении платные.', 'explain_service_scope')
        s = say(s, CASHBACK, 'explain_cashback_mechanics')
        self.assertEqual(s['history'][-1]['product_question_id'], 'benefit_relevance')

    def test_new_attempt_refreshes_materials_and_marks_older_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            app = TrainerApp(Path(directory) / 'sessions.sqlite3', lambda: object())
            old = app.create({'mode':'guided','ending':'appointment'})
            original = app.get(old['id'])
            updated = deepcopy(app.bundle)
            updated['scenario']['version'] += '-updated'
            with patch('do_vstrechi.webapp.load_bundle', return_value=updated):
                new = app.create({'mode':'guided','ending':'appointment'})
            self.assertTrue(app.public(original)['version_notice'])
            self.assertFalse(new['version_notice'])
            self.assertEqual(app.get(old['id']), original)

    def test_review_adds_only_linked_real_parts_to_a_concrete_quantitative_quote(self):
        from test_evaluation import valid_payload
        s = say(start(), CASHBACK, 'explain_cashback_mechanics')
        s = say(s, LIMIT, 'explain_cashback_limits')
        s.update(status='ended', phase='ended', end_reason='employee_stopped')
        payload = valid_payload(s)
        for criterion in payload['criteria']:
            if criterion['id'] in {'C02','C03'}:
                criterion['evidence'] = [{'turn_id':'t03-employee'}]
        result = finalize_evaluation(payload, s)
        for code in ('C02','C03'):
            criterion = next(c for c in result['criteria'] if c['id']==code)
            self.assertEqual(criterion['status'], 'met')
            self.assertTrue(criterion['evidence_context_added'])
            self.assertEqual({e['turn_id'] for e in criterion['evidence']}, {'t03-employee','t04-employee'})
            self.assertEqual(criterion['model_evidence'][0]['turn_id'], 't03-employee')

    def test_review_never_repairs_a_vague_slogan_with_unrelated_numbers(self):
        from test_evaluation import valid_payload
        s = say(start(), 'Много кешбэка.', 'explain_cashback')
        s = say(s, CASHBACK + ' ' + LIMIT, 'explain_cashback_mechanics', 'explain_cashback_limits')
        s.update(status='ended', phase='ended', end_reason='employee_stopped')
        payload = valid_payload(s)
        for criterion in payload['criteria']:
            if criterion['id'] in {'C02','C03'}:
                criterion['evidence'] = [{'turn_id':'t03-employee'}]
        result = finalize_evaluation(payload, s)
        for code in ('C02','C03'):
            criterion = next(c for c in result['criteria'] if c['id']==code)
            self.assertEqual(criterion['status'], 'requires_review')
            self.assertFalse(criterion.get('evidence_context_added'))


if __name__ == '__main__': unittest.main()
