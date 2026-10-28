from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from do_vstrechi.contracts import ContractError
from do_vstrechi.data import fingerprint
from do_vstrechi.engine import review_packet
from do_vstrechi.evaluation import evaluation_context, evaluation_schema, finalize_evaluation, validate_evaluation
from test_evaluation import completed_session, valid_payload


def speech_case(kind='familiarity',level='needs_improvement',text='Ну чё, забираете карту?'):
    session=completed_session()
    payload=valid_payload(session)
    session['history'].insert(-1,{'id':'e3','role':'employee','text':text})
    speech=next(c for c in payload['criteria'] if c['id']=='C10')
    speech.update(status='not_met',evidence=[{'turn_id':'e3'}],reason='Форма обращения не соответствует деловому разговору.',next_step='')
    payload['speech_review']={'level':level,'summary':'Нужно изменить форму обращения.',
        'findings':[{'kind':kind,'evidence':{'turn_id':'e3'},'reason':'Фамильярное обращение к клиенту.',
                     'suggested_phrase':'Оставим сегодняшнюю встречу для получения карты?'}]}
    return session,payload


class SpeechReviewTests(unittest.TestCase):
    def test_policy_and_schema_are_available_only_for_new_criterion(self):
        session=completed_session()
        self.assertIn('speech_policy',evaluation_context(session))
        self.assertIn('speech_review',evaluation_schema(session)['required'])
        session['materials']['rubric']['criteria']=[c for c in session['materials']['rubric']['criteria'] if c['id']!='C10']
        session['materials']['rubric'].pop('speech_policy')
        self.assertNotIn('speech_policy',evaluation_context(session))
        self.assertNotIn('speech_review',evaluation_schema(session)['properties'])
        self.assertEqual(len(finalize_evaluation(valid_payload(session),session)['criteria']),9)

    def test_style_failure_has_quote_explanation_replacement_and_affects_rating(self):
        session,payload=speech_case()
        result=finalize_evaluation(payload,session)
        self.assertEqual(result['training_verdict'],'retry')
        self.assertLess(result['rating']['value'],100)
        self.assertEqual(result['speech_review']['level'],'needs_improvement')
        self.assertEqual(result['speech_review']['findings'][0]['evidence']['quote'],'Ну чё, забираете карту?')
        self.assertEqual(next(c for c in result['criteria'] if c['id']=='C10')['next_step'],
                         'Оставим сегодняшнюю встречу для получения карты?')

    def test_severe_offense_cannot_pass_even_if_other_criteria_met(self):
        session,payload=speech_case('insult','unacceptable','Вы идиот.')
        result=finalize_evaluation(payload,session)
        self.assertEqual(result['training_verdict'],'retry')
        self.assertEqual(result['speech_review']['level'],'unacceptable')

    def test_contradictory_positive_criterion_is_not_silently_accepted(self):
        session,payload=speech_case()
        next(c for c in payload['criteria'] if c['id']=='C10')['status']='met'
        result=finalize_evaluation(payload,session)
        self.assertEqual(result['training_verdict'],'requires_review')
        self.assertEqual(result['speech_review']['level'],'requires_review')

    def test_style_comment_without_specific_finding_requires_review(self):
        session,payload=speech_case()
        payload['speech_review']['findings']=[]
        result=finalize_evaluation(payload,session)
        self.assertEqual(next(c for c in result['criteria'] if c['id']=='C10')['status'],'requires_review')

    def test_matching_speech_level_alias_is_normalized_with_original_status_preserved(self):
        session,payload=speech_case()
        next(c for c in payload['criteria'] if c['id']=='C10')['status']='needs_improvement'
        result=finalize_evaluation(payload,session)
        criterion=next(c for c in result['criteria'] if c['id']=='C10')
        self.assertEqual(criterion['status'],'not_met')
        self.assertEqual(criterion['wire_status'],'needs_improvement')
        self.assertEqual(result['training_verdict'],'retry')
        payload['speech_review']['level']='appropriate'
        with self.assertRaises(ContractError): validate_evaluation(payload,session)

    def test_client_or_invented_evidence_cannot_be_used_as_employee_lexis(self):
        for bad_id in ['c1','not-in-history']:
            session,payload=speech_case()
            payload['speech_review']['findings'][0]['evidence']={'turn_id':bad_id}
            with self.assertRaises(ContractError): validate_evaluation(payload,session)

    def test_fabricated_quote_and_additional_keys_are_rejected(self):
        session,payload=speech_case()
        payload['speech_review']['findings'][0]['evidence']['quote']='Слова, которых не было.'
        with self.assertRaises(ContractError): validate_evaluation(payload,session)
        session,payload=speech_case()
        payload['speech_review']['findings'][0]['score']=0
        with self.assertRaises(ContractError): validate_evaluation(payload,session)

    def test_no_keyword_penalty_for_fillers_typos_or_quoted_client_slang(self):
        for text in ['Ну, давайте коротко поясню условия.','Понятно. Уточню, что вас смущает.',
                     'Вы спросили, что значит «не парьтесь». Поясню условия карты.',
                     'Здраствуйте, я представитель ВТБ.']:
            session=completed_session()
            payload=valid_payload(session)
            session['history'].insert(-1,{'id':'e3','role':'employee','text':text})
            result=finalize_evaluation(payload,session)
            self.assertEqual(next(c for c in result['criteria'] if c['id']=='C10')['status'],'met')

    def test_speech_cannot_be_hidden_as_inapplicable_when_employee_spoke(self):
        session=completed_session();payload=valid_payload(session)
        payload['speech_review']['level']='not_applicable'
        next(c for c in payload['criteria'] if c['id']=='C10')['status']='not_applicable'
        result=finalize_evaluation(payload,session)
        self.assertEqual(result['speech_review']['level'],'requires_review')

    def test_review_exposes_feedback_and_unassessed_preview_has_employee_evidence(self):
        session,payload=speech_case()
        preview=review_packet(session)
        self.assertTrue(next(c for c in preview['criteria'] if c['id']=='C10')['evidence_candidates'])
        session['evaluation']=finalize_evaluation(payload,session)
        review=review_packet(session)
        self.assertEqual(review['speech_review']['level'],'needs_improvement')
        self.assertEqual(review['speech_review']['findings'][0]['evidence']['turn_id'],'e3')

    def test_unknown_comparison_stays_a_separate_factual_issue(self):
        session,payload=speech_case('categorical_promise','needs_improvement','В ВТБ в любом случае условия лучше.')
        payload['claims'].append({'evidence':{'turn_id':'e3'},'status':'not_established','explanation':'Условия другой карты неизвестны.','facts':[],'correction':[]})
        result=finalize_evaluation(payload,session)
        self.assertEqual(result['speech_review']['level'],'needs_improvement')
        self.assertEqual(result['claims'][-1]['status'],'not_established')


if __name__=='__main__': unittest.main()
