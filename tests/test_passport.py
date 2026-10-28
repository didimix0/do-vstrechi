from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from do_vstrechi.client import client_context, validate_client_reply
from do_vstrechi.contracts import ContractError, bind_action_evidence
from do_vstrechi.data import validate_bundle
from legacy_fixtures import load_bundle
from do_vstrechi.engine import advance, new_session, review_packet


def step(session, *codes, text='Подскажите, оригинал паспорта РФ будет у вас с собой на встрече?'):
    return advance(session, text, {'actions': [{'code': code, 'quote': text} for code in codes]}, mode='test')


class PassportTests(unittest.TestCase):
    def setUp(self):
        self.bundle = load_bundle()
        self.bundle['scenario']['dialogue_features'].pop('product_questions', None)
        self.session = step(new_session(self.bundle), 'introduce', text='Здравствуйте, я представитель ВТБ, звоню перед встречей.')

    def ready(self, ending='appointment'):
        session = deepcopy(self.session)
        session['ending_preference'] = ending
        session = step(session, 'explain_service', text='Выпуск и обслуживание карты бесплатные.')
        return step(session, 'propose_next_step', text='Сохраним встречу?')

    def test_confirmed_details_leave_room_for_passport_without_hint(self):
        session = step(self.ready(), 'confirm_time', 'confirm_address', text='Встречаемся сегодня в восемнадцать по согласованному адресу?')
        self.assertEqual(session['status'], 'active')
        self.assertEqual(session['client_outcome'], 'appointment_agreed')
        self.assertEqual(session['history'][-1]['reaction'], 'details_confirmed')
        self.assertNotIn('паспорт', session['history'][-1]['text'].lower())
        session = step(session, 'ask_passport_availability')
        self.assertEqual(session['end_reason'], 'appointment_confirmed')
        self.assertEqual(session['history'][-1]['reaction'], 'passport_appointment')
        packet = review_packet(session)
        self.assertTrue(packet['passport_evidence']['client_answer_recorded'])
        evidence = next(c for c in packet['criteria'] if c['id'] == 'C09')['evidence_candidates']
        self.assertEqual(evidence[0]['quote'], session['history'][-2]['text'])
        self.assertEqual(packet['training_verdict'], 'not_evaluated')

    def test_early_passport_answer_does_not_commit_to_meeting(self):
        session = step(self.session, 'ask_passport_availability')
        self.assertEqual(session['history'][-1]['reaction'], 'passport_objection')
        self.assertEqual(session['client_outcome'], 'undecided')
        self.assertTrue(session['flags']['passport_answered'])
        session = step(session, 'explain_service', text='Обслуживание бесплатное.')
        session = step(session, 'propose_next_step', 'confirm_time', 'confirm_address', text='Сохраняем встречу сегодня в восемнадцать по согласованному адресу?')
        self.assertEqual(session['status'], 'ended')
        self.assertEqual(session['history'][-1]['reaction'], 'appointment')

    def test_passport_question_can_be_combined_with_introduction(self):
        session = step(new_session(self.bundle), 'introduce', 'ask_passport_availability', text='Я представитель ВТБ. У вас оригинал паспорта будет с собой на встрече?')
        self.assertEqual(session['history'][-1]['reaction'], 'passport_objection')
        self.assertEqual(session['status'], 'active')
        self.assertEqual(session['client_outcome'], 'undecided')

    def test_passport_answer_keeps_missing_time_and_address_questions(self):
        for codes, follow_up in [((), 'ask_time'), (('confirm_time',), 'ask_address')]:
            with self.subTest(follow_up=follow_up):
                session = step(self.ready(), 'ask_passport_availability', *codes)
                self.assertEqual(session['status'], 'active')
                self.assertEqual(session['history'][-1]['continuation_reaction'], follow_up)
                context = client_context(session)
                self.assertTrue(context['known_personal_facts']['original_passport_available'])
                self.assertEqual(context['continuation_reaction'], follow_up)
                validate_client_reply({'reaction': 'passport_available', 'text': session['history'][-1]['text']}, context)

    def test_omitted_question_is_preserved_for_review_without_forcing_pass(self):
        session = step(self.ready(), 'confirm_time', 'confirm_address')
        session = step(session, 'end_meeting', text='Спасибо, до встречи.')
        packet = review_packet(session)
        self.assertEqual(session['status'], 'ended')
        self.assertFalse(packet['passport_evidence']['question_detected'])
        self.assertFalse(next(c for c in packet['criteria'] if c['id'] == 'C09')['evidence_candidates'])
        self.assertEqual(packet['training_verdict'], 'not_evaluated')

    def test_refusal_and_hangup_do_not_turn_into_passport_confirmation(self):
        for ending in ('decline', 'rude_decline', 'hangup'):
            with self.subTest(ending=ending):
                session = self.ready(ending)
                if session['status'] == 'active':
                    session = step(session, 'ask_passport_availability')
                self.assertFalse(session['flags']['passport_answered'])
                self.assertNotEqual(session['client_outcome'], 'appointment_agreed')
                self.assertEqual(review_packet(session)['training_verdict'], 'not_evaluated')

    def test_passport_does_not_resolve_an_unanswered_product_question(self):
        session = step(self.session, 'explain_cashback', text='Есть кешбэк по категориям.')
        session = step(session, 'ask_passport_availability', 'propose_next_step')
        self.assertFalse(session['flags']['passport_answered'])
        self.assertEqual(session['history'][-1]['reaction'], 'missing_service')
        session = step(self.session, 'possible_product_error', 'ask_passport_availability')
        self.assertEqual(session['history'][-1]['reaction'], 'source_question')
        self.assertFalse(session['flags']['passport_answered'])

    def test_natural_client_cannot_hint_or_invent_missing_document(self):
        session = step(self.ready(), 'confirm_time', 'confirm_address')
        context = client_context(session)
        for text in ('А паспорт нужен?', 'Всё верно, до встречи!'):
            with self.subTest(text=text), self.assertRaises(ContractError):
                validate_client_reply({'reaction': 'details_confirmed', 'text': text}, context)
        session = step(session, 'ask_passport_availability')
        context = client_context(session)
        with self.assertRaises(ContractError):
            validate_client_reply({'reaction': 'passport_appointment', 'text': 'Паспорта нет, будет только копия.'}, context)
        validate_client_reply({'reaction': 'passport_appointment', 'text': 'Да, оригинал паспорта возьму. Тогда до встречи.'}, context)

    def test_old_materials_keep_the_previous_completion_rule(self):
        legacy = deepcopy(self.bundle)
        del legacy['scenario']['dialogue_features']
        legacy['scenario']['opening'] = 'Алло.'
        session = step(new_session(legacy), 'introduce')
        session = step(session, 'explain_service')
        session = step(session, 'propose_next_step', 'confirm_time', 'confirm_address')
        self.assertEqual(session['status'], 'ended')
        self.assertNotIn('passport_answered', session['flags'])

    def test_enabled_feature_requires_an_explicit_synthetic_fact_and_replies(self):
        for key in ('original_passport_available',):
            broken = deepcopy(self.bundle)
            del broken['scenario']['persona'][key]
            with self.assertRaises(ValueError):
                validate_bundle(broken)
        broken = deepcopy(self.bundle)
        del broken['scenario']['replies']['details_confirmed']
        with self.assertRaises(ValueError):
            validate_bundle(broken)

    def test_simple_reminder_cannot_be_counted_as_question_even_if_model_labels_it(self):
        for text in ('Подготовьте паспорт к встрече.', 'Пожалуйста, возьмите паспорт.', 'Не забудьте оригинал паспорта, он будет нужен.'):
            with self.subTest(text=text):
                analysis = bind_action_evidence({'actions': [{'code': 'ask_passport_availability'}]}, text)
                self.assertEqual(analysis['actions'], [{'code': 'remind_passport', 'quote': text}])
        text = 'Встречаемся в восемнадцать. Подготовьте паспорт.'
        analysis = bind_action_evidence({'actions': [{'code': 'confirm_time'}, {'code': 'ask_passport_availability'}]}, text)
        self.assertEqual([a['code'] for a in analysis['actions']], ['confirm_time', 'remind_passport'])

    def test_reminder_gets_a_natural_response_but_does_not_fill_question_criterion(self):
        session = step(self.ready(), 'confirm_time', 'confirm_address')
        session = step(session, 'remind_passport', text='Подготовьте паспорт к встрече.')
        self.assertEqual(session['history'][-1]['reaction'], 'passport_available')
        self.assertEqual(session['status'], 'active')
        self.assertFalse(session['flags']['passport_question_detected'])
        self.assertFalse(session['flags']['passport_answered'])
        self.assertFalse(next(c for c in review_packet(session)['criteria'] if c['id'] == 'C09')['evidence_candidates'])

    def test_reminder_guard_preserves_genuine_questions_in_combined_turns(self):
        for text in ('Оригинал паспорта РФ будет с собой?', 'У вас есть паспорт',
                     'Подготовьте паспорт. Оригинал будет при себе?', 'Паспорт возьмёте с собой'):
            with self.subTest(text=text):
                analysis = bind_action_evidence({'actions': [{'code': 'ask_passport_availability'}]}, text)
                self.assertEqual(analysis['actions'][0]['code'], 'ask_passport_availability')


if __name__ == '__main__':
    unittest.main()
