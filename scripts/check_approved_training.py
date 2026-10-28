"""Проверка согласованных образцов и деловой речи. --live обращается к GigaChat."""
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from do_vstrechi.client import client_context
from do_vstrechi.conversation import naturalize_client_reply
from do_vstrechi.contracts import ContractError
from do_vstrechi.data import load_bundle
from do_vstrechi.engine import advance, new_session, review_packet
from do_vstrechi.evaluation import apply_evaluation
from do_vstrechi.gigachat import GigaChatAnalyzer, ProviderError, load_local_environment
from do_vstrechi.webapp import OPTIONS


class Capture(GigaChatAnalyzer):
    last_output=None
    def _structured(self,*args,**kwargs):
        result=super()._structured(*args,**kwargs)
        self.last_output=deepcopy(result)
        return result


def holdout_sessions():
    source=json.loads((ROOT/'scripts/cases/dialogue_holdout.v1.json').read_text(encoding='utf-8'))
    bundle=load_bundle()
    reactions={'DV-04':'missing_explanation','DV-08':'product_question','DV-12':'source_question',
               'DV-16':'source_question','DV-20':'source_question','DV-24':'detail_answered',
               'DV-28':'unresolved_ack','DV-32':'details_confirmed','DV-36':'appointment'}
    for case in source['cases']:
        s=new_session(bundle);s['client_reply_mode']='natural'
        s['flags']['introduced']=True
        s['history']=[{'id':f'h{i}','role':t['role'],'text':t['text']} for i,t in enumerate(case['history'])]
        s['history'].append({'id':'employee-current','role':'employee','text':case['employee_turn']})
        if case['id']=='DV-40':
            s.update(status='ended',phase='ended',end_reason='client_hangup',client_outcome='call_ended_by_client')
            s['history'].append({'id':'hangup','role':'event','type':'client_hangup','text':'Клиент завершил звонок.'})
            yield case['id'],s
            continue
        reaction=reactions[case['id']]
        reply=bundle['scenario']['replies'].get(reaction,'Проверочная реакция.')
        last={'id':'client-current','role':'client','reaction':reaction,'text':reply,'reply_source':'scripted'}
        if case['id']=='DV-04': s['flags']['brief_accepted']=True
        if case['id']=='DV-08':
            last.update(product_question_id='benefit_relevance')
            definition=next(q for q in bundle['scenario']['product_questions'] if q['id']=='benefit_relevance')
            last['text']=definition['text']
            s['flags']['service_discussed']=True
            s['product_question_state']['covered']=['service_scope']
            s['product_question_state']['records']=[{'question_id':'benefit_relevance','question_meaning':definition['text'],
                'status':'open','asked_turn_ids':['client-current'],'answer_turn_ids':[]}]
        if reaction=='source_question': s['flags']['pending_product_clarification']=True
        if case['id']=='DV-24':
            s['flags']['cashback_discussed']=True
            s['product_question_state']['covered']=['cashback_limit']
        if case['id']=='DV-28':
            s['flags'].update(service_discussed=True,cashback_discussed=True)
            definition=next(q for q in bundle['scenario']['product_questions'] if q['id']=='cashback_mechanics')
            s['product_question_state']['records']=[{'question_id':'cashback_mechanics','question_meaning':definition['text'],
                'status':'deferred','asked_turn_ids':['h0'],'answer_turn_ids':['h1']}]
        if case['id'] in {'DV-32','DV-36'}:
            s['flags'].update(meeting_ready=True,time_discussed=True,address_discussed=True,
                              passport_answered=(case['id']=='DV-36'))
        if case['id']=='DV-36': s.update(status='ended',phase='ended',end_reason='appointment_confirmed')
        s['history'].append(last)
        yield case['id'],s


def professional_session():
    s=new_session(load_bundle())
    for key in ['intro','offer','service','benefit','meeting','both','passport']:
        text,codes=OPTIONS[key]
        text=text.format(**s['materials']['scenario']['appointment'])
        s=advance(s,text,{'actions':[{'code':code,'quote':text} for code in codes]},mode='synthetic')
    assert s['status']=='ended'
    return s


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--clients-only',action='store_true')
    parser.add_argument('--speech-only',action='store_true')
    parser.add_argument('--case')
    parser.add_argument('--output',default='var/approved-training-live.json')
    args=parser.parse_args()
    sessions=list(holdout_sessions())
    if args.case: sessions=[p for p in sessions if p[0]==args.case]
    if not args.live:
        for code,s in sessions:
            if code!='DV-40':
                context=client_context(s)
                assert code not in json.dumps(context,ensure_ascii=False)
        print(json.dumps({'status':'offline_fixtures_valid','holdout_cases':len(sessions),'network_used':False}))
        return 0
    load_local_environment();provider=Capture()
    report={'created_at':datetime.now(timezone.utc).isoformat(),'synthetic':True,
            'scope':'Client generation on prepared controller states and two separate full-call evaluations; not a real user study or complete end-to-end benchmark.',
            'cases':[],'status':'running'}
    target=ROOT/args.output
    def record(item):
        report['cases'].append(item)
        target.parent.mkdir(exist_ok=True,parents=True)
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:item[k] for k in ('id','ok','client','level','error') if k in item},ensure_ascii=False),flush=True)
    if not args.speech_only:
        for code,s in sessions:
            item={'id':code,'kind':'held_out_client','employee':s['history'][-2 if code=='DV-40' else -2]['text']}
            provider.last_output=None
            try:
                context=client_context(s) if code!='DV-40' else None
                if context: assert code not in json.dumps(context,ensure_ascii=False)
                updated=naturalize_client_reply(s,provider)
                if code=='DV-40':
                    item.update(ok=updated==s and provider.last_output is None,network_call=False)
                else:
                    last=updated['history'][-1]
                    item.update(client=last['text'],reaction=last['reaction'],source=last['reply_source'],
                                raw_output=provider.last_output,metrics=deepcopy(updated['calls'][-1]))
                    ok=last['reply_source']=='generated'
                    if code=='DV-24': ok &= '?' not in last['text'] and bool(re.search(r'поня|ясн|спасиб|хорош|теперь',last['text'],re.I))
                    if code=='DV-36': ok &= not bool(re.search(r'отмен|отказ|не\s+нужн',last['text'],re.I))
                    item['ok']=bool(ok)
            except (ProviderError,ContractError,AssertionError) as exc:
                item.update(ok=False,error=str(exc),raw_output=provider.last_output)
            record(item)
    if not args.clients_only and not args.case:
        for name,extra in [('professional',None),('familiar_and_categorical','Ну чё, забираете карту? В ВТБ в любом случае условия лучше.')]:
            s=professional_session()
            if extra:
                s['history'].insert(-1,{'id':'style-extra','role':'employee','text':extra})
            item={'id':name,'kind':'speech_evaluation'}
            provider.last_output=None
            try:
                updated=apply_evaluation(s,provider)
                packet=review_packet(updated)
                criterion=next(c for c in packet['criteria'] if c['id']=='C10')
                level=packet['speech_review']['level']
                item.update(level=level,criterion_status=criterion['status'],training_verdict=packet['training_verdict'],
                            rating=packet['rating'],evaluation=updated['evaluation'],raw_output=provider.last_output,
                            metrics=deepcopy(provider.metrics))
                item['ok']=(level=='appropriate' and criterion['status']=='met') if not extra else (
                    level=='needs_improvement' and criterion['status']=='not_met' and bool(packet['speech_review']['findings'])
                    and packet['training_verdict']!='passed')
            except (ProviderError,ContractError) as exc:
                item.update(ok=False,error=str(exc),raw_output=provider.last_output,metrics=deepcopy(provider.metrics))
            record(item)
    report['status']='ok' if all(c['ok'] for c in report['cases']) else 'needs_review'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'report':str(target)},ensure_ascii=False),flush=True)
    return 0 if report['status']=='ok' else 1


if __name__=='__main__': raise SystemExit(main())
