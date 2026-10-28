from copy import deepcopy
from pathlib import Path
import sys, tempfile, unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from do_vstrechi.data import load_bundle, validate_bundle, fingerprint
from do_vstrechi.engine import advance, new_session
from do_vstrechi.client import client_context, validate_client_reply
from do_vstrechi.contracts import ContractError
from do_vstrechi.contracts import bind_action_evidence
from do_vstrechi.conversation import naturalize_client_reply
from do_vstrechi.gigachat import ProviderError
from do_vstrechi.webapp import TrainerApp, ApiError


def say(session,text,*codes):
    return advance(session,text,{'actions':[{'code':c,'quote':text} for c in codes]},mode='test')


class CharacterTests(unittest.TestCase):
    def test_explicit_meeting_question_is_not_lost_behind_details(self):
        text='Сохраняем нашу встречу, чтобы подробнее посмотреть условия?'
        result=bind_action_evidence({'actions':[{'code':'defer_to_meeting'}]},text)
        self.assertIn('propose_next_step',[a['code'] for a in result['actions']])
        for text in ('Не сохраняем встречу?', 'Вы сказали «сохраняем встречу»?', 'У нас сегодня назначена встреча. Всё в силе?'):
            result=bind_action_evidence({'actions':[{'code':'unclear'}]},text)
            self.assertNotIn('propose_next_step',[a['code'] for a in result['actions']])

    def test_same_complete_path_and_materials_for_all_characters(self):
        with tempfile.TemporaryDirectory() as folder:
            app=TrainerApp(Path(folder)/'test.sqlite3')
            for character in ('calm','hurried','skeptical'):
                s=new_session(app.bundle,character=character)
                path=('intro','offer','service','benefit','meeting','transfers','withdrawals')
                if character=='skeptical':path+=('withdrawal_limits','withdrawal_fee_base')
                for key in path+('both','passport'):
                    text,codes=app.option_value(s,key);s=say(s,text,*codes)
                self.assertEqual(s['client_outcome'],'appointment_agreed')
                self.assertEqual(s['status'],'ended')
                self.assertEqual(s['material_hashes']['product'],fingerprint(app.bundle['product']))
                self.assertEqual(s['material_hashes']['rubric'],fingerprint(app.bundle['rubric']))

    def test_character_frozen_and_context_has_no_hidden_tariffs(self):
        bundle=load_bundle();s=new_session(bundle,character='skeptical')
        bundle['scenario']['client_profiles'][2]['instruction']='Изменённые инструкции'
        s=say(s,'Здравствуйте. Звоню по поводу встречи.','introduce')
        context=client_context(s)
        self.assertNotIn('Изменённые инструкции',context['persona']['tone'])
        self.assertEqual(context['persona']['tone'],s['client_profile']['instruction'])
        self.assertIn('ограничения',context['language_style']['character'])
        self.assertNotIn('reference_reply',context)
        self.assertNotIn('product',context)
        self.assertTrue(s['history'][-1]['text'].startswith('Здравствуйте'))

    def test_duplicate_client_reply_is_rejected_and_fallback_varies(self):
        s=new_session(load_bundle());s=say(s,'Здравствуйте, звоню по поводу встречи.','introduce')
        s=say(s,'Можно кратко рассказать об условиях?','offer_brief_explanation')
        duplicate='Хорошо, я вас слушаю, расскажите только самое главное.'
        s['history'][-1]['text']=duplicate
        s=say(s,'Можно кратко рассказать об условиях?','offer_brief_explanation')
        with self.assertRaises(ContractError):validate_client_reply({'reaction':s['history'][-1]['reaction'],'text':duplicate},client_context(s))
        s['client_reply_mode']='natural'
        class Broken:
            metrics={}
            def generate_client_reply(self,session):raise ProviderError('Недоступен')
        updated=naturalize_client_reply(s,Broken())
        self.assertEqual(updated['history'][-1]['reply_source'],'scripted_fallback')
        self.assertNotEqual(updated['history'][-1]['text'],duplicate)
        self.assertEqual(updated['flags'],s['flags'])

    def test_invalid_character_rejected_and_legacy_creation_supported(self):
        with tempfile.TemporaryDirectory() as folder:
            app=TrainerApp(Path(folder)/'test.sqlite3')
            s=app.create({'mode':'guided','ending':'appointment'})
            self.assertEqual(s['character']['id'],'calm')
            with self.assertRaises(ApiError):app.create({'mode':'guided','ending':'appointment','character':[]})
            bundle=load_bundle();bundle['scenario']['client_profiles'][1]['id']='calm'
            with self.assertRaises(ValueError):validate_bundle(bundle)

    def test_profile_cannot_reopen_final_refusal(self):
        for character in ('calm','hurried','skeptical'):
            s=new_session(load_bundle(),character=character)
            s=say(s,'Здравствуйте, звоню по поводу встречи.','introduce')
            s=say(s,'Вы должны забрать карту.','pressure')
            s=say(s,'Давайте ещё расскажу.','offer_brief_explanation')
            s=say(s,'Давайте ещё расскажу.','offer_brief_explanation')
            self.assertEqual(s['status'],'ended')
            self.assertEqual(s['client_outcome'],'declined')
