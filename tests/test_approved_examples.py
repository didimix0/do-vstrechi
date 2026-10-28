from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from do_vstrechi.client import client_context, validate_client_reply
from do_vstrechi.data import validate_bundle
from legacy_fixtures import load_bundle
from do_vstrechi.dialogue_examples import CHECK_IDS
from do_vstrechi.engine import new_session
from do_vstrechi.gigachat import GigaChatAnalyzer


def context_fixture(reaction, *, question=None, covered=(), open_topics=(), employee='Проверочная реплика', previous_client='Я передумал получать карту.', flags=None):
    session = new_session(load_bundle())
    session['flags'].update(introduced=True)
    session['flags'].update(flags or {})
    state = session['product_question_state']
    state['covered'] = list(covered)
    for topic in open_topics:
        definition = next(q for q in session['materials']['scenario']['product_questions'] if q['id']==topic)
        state['records'].append({'question_id':topic,'question_meaning':definition['text'],'status':'open',
                                 'asked_turn_ids':['old-question'],'answer_turn_ids':[]})
    if question:
        state['active_id'] = question
    session['history'] = [{'id':'old-question','role':'client','text':previous_client},
                          {'id':'e1','role':'employee','text':employee},
                          {'id':'c1','role':'client','text':'Проверочная реакция','reaction':reaction}]
    if question: session['history'][-1]['product_question_id']=question
    return session


class ApprovedExampleTests(unittest.TestCase):
    def test_pack_contains_only_30_approved_examples_with_10_checks_reserved(self):
        pack=load_bundle()['dialogue_examples']
        self.assertEqual(len(pack['cases']),30)
        self.assertTrue(pack['approved'])
        self.assertFalse({c['id'] for c in pack['cases']} & CHECK_IDS)
        self.assertTrue(all(c['approval']['status']=='approved' for c in pack['cases']))

    def test_unapproved_mismatched_or_holdout_materials_cannot_be_loaded(self):
        for change in ['approval','product','holdout']:
            bundle=load_bundle()
            if change=='approval': bundle['dialogue_examples']['approved']=False
            elif change=='product': bundle['dialogue_examples']['product_version']='unknown'
            else: bundle['dialogue_examples']['cases'][0]['id']='DV-04'
            with self.assertRaises(ValueError): validate_bundle(bundle)

    def test_context_uses_only_current_reaction_and_does_not_leak_review_data(self):
        session=context_fixture('objection')
        before=deepcopy(session)
        context=client_context(session)
        self.assertEqual(session,before)
        examples=context['approved_dialogue_examples']['examples']
        self.assertEqual([e['id'] for e in examples],['DV-01'])
        serialized=json.dumps(context,ensure_ascii=False)
        for forbidden in ['held_out_check','review_checks_not_for_client_prompt','employee_claim_status','speech_policy']:
            self.assertNotIn(forbidden,serialized)
        for reserved in CHECK_IDS: self.assertNotIn(reserved,serialized)

    def test_topic_and_answered_memory_gate_examples(self):
        session=context_fixture('product_question',question='benefit_relevance',covered=['service_scope'])
        self.assertEqual([e['id'] for e in client_context(session)['approved_dialogue_examples']['examples']],['DV-25'])
        session['product_question_state']['covered']=[]
        self.assertNotIn('approved_dialogue_examples',client_context(session))

    def test_open_question_selects_answer_and_memory_example(self):
        session=context_fixture('reason',open_topics=['service_scope'])
        ids=[e['id'] for e in client_context(session)['approved_dialogue_examples']['examples']]
        self.assertEqual(ids,['DV-26'])
        session['product_question_state']['records']=[]
        self.assertIn('DV-05',[e['id'] for e in client_context(session)['approved_dialogue_examples']['examples']])

    def test_short_answer_example_does_not_add_unrouted_follow_up(self):
        session=context_fixture('detail_answered',employee='Три тысячи рублей.',previous_client='Какой максимальный кешбэк за месяц?')
        examples=client_context(session)['approved_dialogue_examples']['examples']
        self.assertEqual(examples[0]['id'],'DV-15')
        self.assertEqual(examples[0]['possible_client_replies'],['Хорошо, с месячным максимумом разобрались.'])

    def test_selection_is_bounded_and_never_loads_all_30_into_context(self):
        session=context_fixture('product_question',question='cashback_mechanics',employee='Много кешбэка.')
        context=client_context(session)
        examples=context['approved_dialogue_examples']['examples']
        self.assertLessEqual(len(examples),2)
        self.assertLessEqual(len(json.dumps(examples,ensure_ascii=False)),6000)
        for example in examples:
            for reply in example['possible_client_replies']:
                validate_client_reply({'reaction':'product_question','text':reply},context)

    def test_approved_natural_question_without_cashback_keyword_is_accepted(self):
        context=client_context(context_fixture('product_question',question='cashback_mechanics'))
        text='А от чего зависит процент? Это же не на каждую покупку, правильно?'
        self.assertEqual(validate_client_reply({'reaction':'product_question','text':text},context),text)

    def test_every_routed_approved_reply_satisfies_current_client_contract(self):
        for case in load_bundle()['dialogue_examples']['cases']:
            for route in case['runtime_routes']:
                reaction=route['reactions'][0]
                question=route.get('question_ids',[None])[0]
                session=context_fixture(reaction,question=question,flags=route.get('flags'))
                context=client_context(session)
                variants=case['target_client_behavior']['allowed_reply_variants']
                for i in route.get('variant_indices',range(len(variants))):
                    with self.subTest(example=case['id'],variant=i,reaction=reaction):
                        validate_client_reply({'reaction':reaction,'text':variants[i]},context)

    def test_indirect_clarification_is_allowed_but_plain_statement_is_not_a_question(self):
        from do_vstrechi.contracts import ContractError
        context=client_context(context_fixture('product_question',question='benefit_relevance'))
        text='Мне пока неясно, что я получу от второй карты.'
        self.assertEqual(validate_client_reply({'reaction':'product_question','text':text},context),text)
        with self.assertRaises(ContractError):
            validate_client_reply({'reaction':'product_question','text':'Польза карты мне понятна.'},context)

    def test_legacy_and_compound_passport_turns_do_not_receive_incompatible_examples(self):
        session=context_fixture('passport_available',flags={'meeting_ready':True})
        session['history'][-1]['continuation_reaction']='ask_address'
        self.assertNotIn('approved_dialogue_examples',client_context(session))
        session=context_fixture('objection')
        session['materials']['scenario']['dialogue_features'].pop('approved_examples')
        self.assertNotIn('approved_dialogue_examples',client_context(session))

    def test_examples_are_frozen_in_session_and_metric_records_selected_ids(self):
        bundle=load_bundle()
        session=new_session(bundle)
        bundle['dialogue_examples']['approved']=False
        self.assertTrue(session['materials']['dialogue_examples']['approved'])
        session=context_fixture('objection')
        provider=GigaChatAnalyzer({'GIGACHAT_AUTH_KEY':'synthetic'})
        def structured(prompt,context,*args):
            provider.metrics={'role':'client_generation'}
            self.assertEqual(context['approved_dialogue_examples']['examples'][0]['id'],'DV-01')
            return {'reaction':'objection','text':'Я передумал, своей картой пользуюсь.'}
        with patch.object(provider,'_structured',side_effect=structured): provider.generate_client_reply(session)
        self.assertEqual(provider.metrics['dialogue_example_ids'],['DV-01'])


if __name__=='__main__': unittest.main()
