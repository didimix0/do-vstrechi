from contextlib import redirect_stdout
from copy import deepcopy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from do_vstrechi.cli import main
from do_vstrechi.client import client_context, validate_client_reply
from do_vstrechi.contracts import ContractError
from do_vstrechi.conversation import naturalize_client_reply
from do_vstrechi.data import validate_bundle
from legacy_fixtures import load_bundle
from do_vstrechi.engine import advance, new_session
from do_vstrechi.webapp import TrainerApp


INTRO = 'Здравствуйте, я сотрудник ВТБ, у нас сегодня запланирована встреча. Верно?'

def say(session, text=INTRO, code='introduce'):
    return advance(session, text, {'actions': [{'code': code, 'quote': text}]}, mode='test')


class OpeningTests(unittest.TestCase):
    def test_employee_starts_in_both_web_modes_without_model_request(self):
        with tempfile.TemporaryDirectory() as folder:
            app = TrainerApp(Path(folder) / 'sessions.sqlite3', lambda: object())
            for mode in ('guided', 'gigachat'):
                session = app.create({'mode': mode, 'ending': 'appointment'})
                self.assertEqual(session['history'], [])
                self.assertEqual(session['turn_count'], 0)
                self.assertEqual(app.get(session['id'])['calls'], [])

    def test_first_introduction_has_greeting_then_existing_appointment_and_doubt(self):
        session = say(new_session(load_bundle()))
        self.assertEqual([t['role'] for t in session['history']], ['employee', 'client'])
        reply = session['history'][-1]
        self.assertTrue(reply['text'].startswith('Здравствуйте.'))
        self.assertLess(reply['text'].index('договаривались'), reply['text'].index('передумал'))
        self.assertEqual(reply['reaction'], 'objection')
        self.assertEqual(session['client_outcome'], 'undecided')
        self.assertFalse(session['flags']['meeting_ready'])

    def test_generated_first_reply_requires_greeting_and_keeps_fallback(self):
        session = say(new_session(load_bundle()))
        session['client_reply_mode'] = 'natural'
        context = client_context(session)
        self.assertTrue(context['opening_turn']['greeting_required'])
        for text in ('Я передумал получать карту.', 'Я передумал. Здравствуйте.'):
            with self.assertRaises(ContractError):
                validate_client_reply({'reaction': 'objection', 'text': text}, context)
        for text in ('Здравствуйте. Да, помню, но передумал получать карту.',
                     'Добрый день. Встречу назначали, но карту уже не хочу.',
                     'Да, здравствуйте. Я уже передумал получать карту.'):
            self.assertEqual(validate_client_reply({'reaction': 'objection', 'text': text}, context), text)
        class Ungreeted:
            metrics = {}
            def generate_client_reply(self, session): return 'Я передумал получать карту.'
        result = naturalize_client_reply(session, Ungreeted())
        self.assertEqual(result['history'][-1]['reply_source'], 'scripted_fallback')
        self.assertTrue(result['history'][-1]['text'].startswith('Здравствуйте.'))

    def test_short_greeting_gives_employee_room_to_introduce(self):
        session = say(new_session(load_bundle()), 'Здравствуйте!', 'unclear')
        self.assertEqual(session['history'][-1]['text'], 'Здравствуйте. Слушаю вас.')
        self.assertFalse(session['flags']['introduced'])
        self.assertNotIn('передумал', session['history'][-1]['text'])
        session = say(session)
        self.assertFalse(client_context(session)['opening_turn']['greeting_required'])
        self.assertFalse(session['history'][-1]['text'].startswith('Здравствуйте'))
        self.assertEqual(session['history'][-1]['reaction'], 'objection')

    def test_later_replies_and_initial_goodbye_do_not_require_greeting(self):
        session = say(say(new_session(load_bundle())), 'Давайте кратко расскажу.', 'offer_brief_explanation')
        self.assertEqual(validate_client_reply({'reaction': 'listen', 'text': 'Да, расскажите.'}, client_context(session)), 'Да, расскажите.')
        ended = say(new_session(load_bundle()), 'До свидания.', 'end_meeting')
        self.assertEqual(ended['status'], 'ended')
        self.assertFalse(client_context(ended)['opening_turn']['greeting_required'])

    def test_old_scenario_keeps_its_client_opening_and_contract(self):
        old = load_bundle()
        old['scenario']['dialogue_features'].pop('employee_first')
        old['scenario']['opening'] = 'Алло.'
        session = new_session(old)
        old['scenario']['opening'] = 'Изменённый источник'
        self.assertEqual(session['history'][0]['text'], 'Алло.')
        session = say(session)
        self.assertNotIn('opening_turn', client_context(session))
        self.assertEqual(validate_client_reply({'reaction': 'objection', 'text': 'Я передумал получать карту.'}, client_context(session)), 'Я передумал получать карту.')

    def test_empty_history_does_not_call_client_model(self):
        session = new_session(load_bundle())
        session['client_reply_mode'] = 'natural'
        self.assertEqual(naturalize_client_reply(session, object()), session)
        with self.assertRaises(ContractError): client_context(session)
        bad = deepcopy(session['materials'])
        bad['scenario']['first_objection_reply'] = 'Я передумал.'
        with self.assertRaises(ValueError): validate_bundle(bad)

    def test_cli_demo_handles_empty_start_and_prints_employee_first(self):
        with tempfile.TemporaryDirectory() as folder:
            output = io.StringIO()
            with redirect_stdout(output), patch('sys.argv', ['run.py', '--demo', '--database', str(Path(folder) / 'demo.sqlite3')]):
                self.assertEqual(main(), 0)
            text = output.getvalue()
            self.assertIn('первая реплика за вами', text)
            self.assertLess(text.index('Сотрудник:'), text.index('Клиент:'))


if __name__ == '__main__': unittest.main()
