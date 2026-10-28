from copy import deepcopy
from pathlib import Path
import sys,tempfile,unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from do_vstrechi.data import load_bundle,validate_bundle
from do_vstrechi.engine import advance,new_session,review_packet
from do_vstrechi.conversation import naturalize_client_reply
from do_vstrechi.webapp import ApiError,TrainerApp


OFFER='Давайте я поподробнее расскажу про условия'
PROMISE='У карты вообще всё бесплатно: карта, уведомления, любые переводы. Наличные тоже можно снимать в любом банкомате без комиссии и без ограничений.'

def say(s,text,*codes):
    return advance(s,text,{'actions':[{'code':c,'quote':text} for c in codes]},mode='test')

def refused_session(bundle=None):
    s=new_session(bundle or load_bundle())
    s=say(s,'Здравствуйте, я представитель ВТБ, звоню по поводу встречи.','introduce')
    return say(s,'Вы всё равно должны получить карту.','pressure')


class BoundedRefusalTests(unittest.TestCase):
    def test_reported_continuations_get_distinct_warning_and_final_goodbye(self):
        s=refused_session();original=s['history'][-1]['text']
        s=say(s,OFFER,'offer_brief_explanation');warning=s['history'][-1]['text']
        self.assertEqual(s['status'],'active')
        self.assertEqual(s['history'][-1]['refusal_stage'],'reminder')
        s=say(s,PROMISE,'possible_product_error');goodbye=s['history'][-1]['text']
        self.assertEqual(len({original,warning,goodbye}),3)
        self.assertEqual(s['history'][-1]['refusal_stage'],'ended')
        self.assertIn('До свидания',goodbye)
        self.assertEqual(s['status'],'ended');self.assertEqual(s['end_reason'],'client_declined')
        self.assertEqual(s['client_outcome'],'declined')
        self.assertFalse(s['flags']['meeting_ready'])
        with self.assertRaises(ValueError):say(s,OFFER,'offer_brief_explanation')

    def test_respecting_refusal_closes_without_forcing_the_warning(self):
        s=say(refused_session(),'Понял, встречу отменю. До свидания.','respect_refusal')
        self.assertEqual(s['status'],'ended')
        self.assertEqual(s['history'][-1]['reaction'],'closed')
        self.assertFalse(any(t.get('refusal_stage')=='reminder' for t in s['history']))

    def test_normal_objection_is_not_treated_as_final_refusal(self):
        s=say(new_session(load_bundle()),'Здравствуйте, представитель ВТБ, звоню по поводу встречи.','introduce')
        s=say(s,OFFER,'offer_brief_explanation')
        self.assertEqual(s['history'][-1]['reaction'],'listen')
        self.assertEqual(s['status'],'active')
        self.assertNotIn('refusal_stage',s['history'][-1])

    def test_previously_saved_loop_ends_on_the_next_continuation(self):
        bundle=load_bundle();bundle['scenario'].pop('refusal_policy')
        s=refused_session(bundle)
        s=say(s,OFFER,'offer_brief_explanation')
        # Уже сохранённая повторная фраза прежней версии не переписывается.
        s['history'][-1]['text']=s['materials']['scenario']['replies']['firm_refusal']
        before=deepcopy(s['history'])
        s=say(s,PROMISE,'possible_product_error')
        self.assertEqual(s['status'],'ended')
        self.assertEqual(s['history'][:len(before)],before)
        self.assertEqual(s['history'][-1]['refusal_continuations'],2)

    def test_refusal_stages_cannot_be_rewritten_or_repeated_by_the_generator(self):
        bundle=load_bundle();bundle['scenario']['dialogue_features']['stable_endings']=False
        s=refused_session(bundle);s['client_reply_mode']='natural'
        for current in (s,say(s,OFFER,'offer_brief_explanation')):
            result=naturalize_client_reply(current,object())
            self.assertEqual(result['history'][-1]['text'],current['history'][-1]['text'])
            self.assertEqual(result['history'][-1]['reply_source'],'scripted_ending')
            self.assertFalse(result.get('client_reply_notice'))

    def test_browser_service_blocks_more_turns_without_an_extra_model_request(self):
        class Provider:
            metrics={};calls=0
            def analyze(self,s,text):
                self.calls+=1
                return {'actions':[{'code':'offer_brief_explanation','quote':text}]}
        with tempfile.TemporaryDirectory() as folder:
            provider=Provider();app=TrainerApp(Path(folder)/'session.sqlite3',lambda:provider)
            s=refused_session();s.update(mode='gigachat',client_reply_mode='natural',requests={})
            app.store.create(s)
            for number in range(2):
                public=app.mutate(s['id'],{'request_id':f'refusal-test-{number}','revision':number,'text':OFFER})
            self.assertEqual(public['status'],'ended');self.assertEqual(public['options'],[])
            self.assertEqual(provider.calls,2)
            with self.assertRaises(ApiError) as failure:
                app.mutate(s['id'],{'request_id':'refusal-test-extra','revision':2,'text':OFFER})
            self.assertEqual(failure.exception.status,409);self.assertEqual(provider.calls,2)
            self.assertFalse(next(c for c in app.review(s['id'])['criteria'] if c['id']=='C07').get('hidden'))

    def test_policy_does_not_allow_unlimited_or_identical_refusal_messages(self):
        for value in (2,True):
            bundle=load_bundle();bundle['scenario']['refusal_policy']['max_reminders']=value
            with self.assertRaises(ValueError):validate_bundle(bundle)
        bundle=load_bundle();bundle['scenario']['refusal_policy']['closing']=bundle['scenario']['refusal_policy']['reminder']
        with self.assertRaises(ValueError):validate_bundle(bundle)


if __name__=='__main__':unittest.main()
