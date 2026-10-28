from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from do_vstrechi.client import client_context, validate_client_reply
from do_vstrechi.contracts import ContractError, bind_action_evidence
from do_vstrechi.data import validate_bundle
from legacy_fixtures import load_bundle
from do_vstrechi.engine import advance, new_session, review_packet
from do_vstrechi.webapp import TrainerApp


def step(session, *codes, text='Учебная реплика сотрудника'):
    return advance(session, text, {'actions': [{'code': code, 'quote': text} for code in codes]}, mode='test')


class ProductQuestionTests(unittest.TestCase):
    def setUp(self):
        self.bundle = load_bundle()
        self.session = step(new_session(self.bundle), 'introduce', text='Я представитель ВТБ, звоню перед встречей.')

    def detailed_service(self):
        return step(self.session, 'explain_service_scope', text='Выпуск и обслуживание карты бесплатные, уведомления отдельно платные при подключении.')

    def complete_explanation(self):
        return step(self.detailed_service(), 'connect_benefit', text='Можно сохранить привычную карту и рассмотреть дополнительную без платы за обслуживание самой карты.')

    def test_free_headline_raises_a_specific_question_that_cannot_be_skipped(self):
        session = step(self.session, 'explain_service', text='Наша карта бесплатная.')
        self.assertEqual(session['history'][-1]['product_question_id'], 'service_scope')
        for codes in [('propose_next_step',), ('ask_passport_availability',), ('unclear',)]:
            with self.subTest(codes=codes):
                result = step(session, *codes)
                self.assertEqual(result['history'][-1]['product_question_id'], 'service_scope')
                self.assertFalse(result['flags']['meeting_ready'])
                self.assertFalse(result['flags']['passport_answered'])
                self.assertEqual(result['client_outcome'], 'undecided')
        record = review_packet(session)['explanation_evidence']['questions'][0]
        self.assertEqual(record['status'], 'open')
        self.assertEqual(record['client_quotes'], [session['history'][-1]['text']])

    def test_cost_scope_does_not_replace_relevant_benefit_and_cashback_is_optional(self):
        session = self.detailed_service()
        self.assertEqual(session['history'][-1]['product_question_id'], 'benefit_relevance')
        session = step(session, 'connect_benefit', text='Можно сохранить привычную карту и рассмотреть дополнительную без платы за обслуживание.')
        self.assertEqual(session['history'][-1]['reaction'], 'detail_answered')
        self.assertFalse(session['flags']['cashback_discussed'])
        self.assertEqual([r['question_id'] for r in session['product_question_state']['records']], ['benefit_relevance'])
        self.assertEqual(session['product_question_state']['records'][0]['status'], 'answer_detected')
        session = step(session, 'propose_next_step', 'confirm_time', 'confirm_address', 'ask_passport_availability')
        self.assertEqual(session['end_reason'], 'appointment_confirmed')
        self.assertEqual(review_packet(session)['training_verdict'], 'not_evaluated')

    def test_cashback_questions_follow_topic_and_keep_memory_of_answers(self):
        session = step(self.complete_explanation(), 'explain_cashback', text='Есть ещё кешбэк.')
        self.assertEqual(session['history'][-1]['product_question_id'], 'cashback_mechanics')
        session = step(session, 'explain_cashback_mechanics', text='Каждый месяц выбираете категории в приложении, получаете возврат за подходящие покупки.')
        self.assertEqual(session['history'][-1]['product_question_id'], 'cashback_limit')
        session = step(session, 'explain_cashback_limits', text='Максимум три тысячи рублей в месяц, а не гарантированная выплата.')
        self.assertEqual(session['history'][-1]['reaction'], 'detail_answered')
        record = next(r for r in review_packet(session)['explanation_evidence']['questions'] if r['question_id'] == 'cashback_limit')
        self.assertEqual(record['employee_quotes'], ['Максимум три тысячи рублей в месяц, а не гарантированная выплата.'])
        session = step(session, 'propose_next_step')
        self.assertEqual(session['history'][-1]['reaction'], 'ask_time')
        self.assertTrue(all(r['status'] == 'answer_detected' for r in session['product_question_state']['records']))

    def test_complete_combined_pitch_avoids_redundant_questions(self):
        session = step(self.session, 'explain_service_scope', 'connect_benefit', 'explain_cashback_mechanics', 'explain_cashback_limits')
        self.assertEqual(session['history'][-1]['reaction'], 'detail_answered')
        self.assertFalse(session['product_question_state']['records'])
        self.assertEqual(len(session['product_question_state']['covered']), 4)
        session = step(session, 'propose_next_step')
        self.assertEqual(session['history'][-1]['reaction'], 'ask_time')

    def test_honest_deferral_is_not_treated_as_a_confirmed_answer(self):
        session = step(self.session, 'explain_service', text='Карта бесплатная.')
        session = step(session, 'connect_benefit', 'defer_product_detail', text='Точный состав платных услуг нужно уточнить. Можно рассмотреть дополнительную карту без платы за её обслуживание.')
        self.assertEqual(session['history'][-1]['reaction'], 'deferred_detail_ack')
        record = review_packet(session)['explanation_evidence']['questions'][0]
        self.assertEqual(record['status'], 'deferred')
        self.assertNotIn('service_scope', session['product_question_state']['covered'])
        session = step(session, 'explain_service_scope', text='Уточнил: выпуск и обслуживание бесплатные, подключённые уведомления платные.')
        record = review_packet(session)['explanation_evidence']['questions'][0]
        self.assertEqual(record['status'], 'answer_detected')
        self.assertEqual(len(record['employee_quotes']), 2)
        self.assertEqual(review_packet(session)['training_verdict'], 'not_evaluated')

    def test_a_possible_error_does_not_silently_cover_detailed_conditions(self):
        session = step(self.session, 'possible_product_error', 'explain_service_scope')
        self.assertFalse(session['product_question_state']['covered'])
        self.assertEqual(session['history'][-1]['reaction'], 'source_question')
        session = step(session, 'clarify_product_terms', 'explain_service_scope')
        self.assertFalse(session['flags']['pending_product_clarification'])
        self.assertIn('service_scope', session['product_question_state']['covered'])
        self.assertEqual(session['history'][-1]['product_question_id'], 'benefit_relevance')

    def test_legacy_service_question_keeps_priority_when_cashback_is_explained_first(self):
        session = step(self.session, 'explain_cashback')
        self.assertEqual(session['history'][-1]['reaction'], 'cashback_question')
        self.assertFalse(session['product_question_state']['records'])
        session = step(session, 'explain_service_scope')
        self.assertEqual(session['history'][-1]['product_question_id'], 'benefit_relevance')

    def test_explicit_closing_and_final_refusal_stop_new_questions(self):
        session = step(self.session, 'explain_service')
        session = step(session, 'end_meeting', text='Спасибо, до свидания.')
        self.assertEqual(session['status'], 'ended')
        self.assertEqual(review_packet(session)['explanation_evidence']['questions'][0]['status'], 'open')
        for ending in ('decline', 'rude_decline', 'hangup'):
            session = self.complete_explanation()
            session['ending_preference'] = ending
            session = step(session, 'propose_next_step')
            if session['status'] == 'active':
                session = step(session, 'explain_cashback')
            self.assertNotEqual(session['history'][-1].get('reaction'), 'product_question')

    def test_generator_gets_only_current_question_and_cannot_answer_for_employee(self):
        session = step(self.session, 'explain_service')
        context = client_context(session)
        self.assertEqual(context['current_question']['id'], 'service_scope')
        for key in ('product', 'rubric', 'product_questions', 'fact_ids'):
            self.assertNotIn(key, context)
        validate_client_reply({'reaction': 'product_question', 'text': 'Что оплачивается отдельно?'}, context)
        for text in ('Всё понятно, буду ждать.', 'Уведомления стоят сто рублей.', 'Во сколько приедете?'):
            with self.subTest(text=text), self.assertRaises(ContractError):
                validate_client_reply({'reaction': 'product_question', 'text': text}, context)

    def test_old_materials_do_not_activate_new_questions(self):
        legacy = deepcopy(self.bundle)
        legacy['scenario']['dialogue_features'].pop('product_questions')
        session = step(new_session(legacy), 'introduce')
        session = step(session, 'explain_service')
        session = step(session, 'propose_next_step')
        self.assertEqual(session['history'][-1]['reaction'], 'ask_time')
        self.assertNotIn('product_question_state', session)

    def test_question_sources_and_actions_are_validated(self):
        for field, values in [('fact_ids', ['NON_EXISTENT']), ('answer_actions', ['invent_answer']), ('criterion_ids', ['C99'])]:
            broken = deepcopy(self.bundle)
            broken['scenario']['product_questions'][0][field] = values
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_bundle(broken)

    def test_guided_mode_can_demonstrate_both_missing_and_complete_explanation(self):
        with tempfile.TemporaryDirectory() as folder:
            app = TrainerApp(Path(folder) / 'sessions.sqlite3')
            session = app.create({'mode': 'guided', 'ending': 'appointment'})
            for index, option in enumerate(('intro', 'offer', 'basic_service', 'service', 'benefit', 'meeting', 'transfers', 'withdrawals', 'both', 'passport')):
                session = app.mutate(session['id'], {'request_id': f'question-check-{index}',
                                                     'revision': session['revision'], 'option_id': option})
            self.assertEqual(session['status'], 'ended')
            report = app.review(session['id'])
            self.assertEqual(report['training_verdict'], 'not_evaluated')
            self.assertTrue(all(r['status'] == 'answer_detected' for r in report['explanation_evidence']['questions']))
            self.assertNotIn('product_question_state', session)

    def test_plain_free_card_is_incomplete_rather_than_a_proven_false_promise(self):
        text = 'Наша карта бесплатная.'
        analysis = bind_action_evidence({'actions': [{'code': 'explain_service'}, {'code': 'possible_product_error'}]}, text)
        self.assertEqual([a['code'] for a in analysis['actions']], ['explain_service'])
        session = advance(self.session, text, analysis, mode='test')
        self.assertEqual(session['history'][-1]['product_question_id'], 'service_scope')
        for text in ('Наша карта бесплатная. Вообще все услуги без комиссии.', 'Никаких комиссий ни за что не будет.'):
            with self.subTest(text=text):
                analysis = bind_action_evidence({'actions': [{'code': 'possible_product_error'}]}, text)
                self.assertEqual(analysis['actions'][0]['code'], 'possible_product_error')

    def test_cost_scope_requires_an_explicit_mention_of_separate_services(self):
        text = 'Можно оставить привычную карту и рассмотреть дополнительную без платы за её обслуживание.'
        analysis = bind_action_evidence({'actions': [{'code': 'explain_service_scope'}, {'code': 'connect_benefit'}]}, text)
        self.assertEqual({a['code'] for a in analysis['actions']}, {'explain_service', 'connect_benefit'})
        text = 'Выпуск и обслуживание карты бесплатны, платные уведомления подключаются отдельно.'
        analysis = bind_action_evidence({'actions': [{'code': 'explain_service_scope'}]}, text)
        self.assertEqual(analysis['actions'][0]['code'], 'explain_service_scope')


if __name__ == '__main__':
    unittest.main()
