"""--live проверяет короткий звонок, вопросы клиента и автоматический разбор через API."""
from copy import deepcopy
from datetime import datetime,timezone
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from do_vstrechi.data import load_bundle
from do_vstrechi.engine import advance,new_session,review_packet
from do_vstrechi.conversation import naturalize_client_reply
from do_vstrechi.evaluation import apply_evaluation
from do_vstrechi.gigachat import GigaChatAnalyzer,load_local_environment
from do_vstrechi.webapp import OPTIONS

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--live',action='store_true');args=parser.parse_args()
    if not args.live:print('No API calls; use tests/test_phone_auto_review.py.');return 0
    load_local_environment();provider=GigaChatAnalyzer()
    report={'created_at':datetime.now(timezone.utc).isoformat(),'synthetic':True,'cases':[],'status':'running'}
    path=ROOT/'var/phone-review-live-2026-10-25.json'
    def save():path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for name in ('short_positive','wrong_promises'):
        s=new_session(load_bundle());s['client_reply_mode']='natural'
        item={'id':name,'turns':[],'ok':False};report['cases'].append(item)
        def send(text):
            nonlocal s
            analysis=provider.analyze(s,text)
            metric=deepcopy(provider.metrics)
            s=advance(s,text,analysis,mode='gigachat',usage=metric.get('usage'))
            s=naturalize_client_reply(s,provider)
            last=s['history'][-1]
            row={'employee':text,'actions':analysis['actions'],'client':last.get('text'),
                 'reaction':last.get('reaction'),'topic':last.get('product_question_id'),'source':last.get('reply_source')}
            item['turns'].append(row);save();print(json.dumps({'case':name,**row},ensure_ascii=False),flush=True)
        try:
            if name=='short_positive':
                for key in ('intro','offer','service','benefit','meeting'):send(OPTIONS[key][0])
                while s['status']=='active' and s['turn_count']<15:
                    topic=s['history'][-1].get('product_question_id')
                    if topic in {'transfers','cash_withdrawal'}:
                        send(OPTIONS['transfers' if topic=='transfers' else 'withdrawals'][0])
                    elif s['history'][-1].get('reaction') in {'ask_time','ask_address'}:
                        send(OPTIONS['both'][0].format(**s['materials']['scenario']['appointment']))
                    elif s['history'][-1].get('reaction')=='details_confirmed':send(OPTIONS['passport'][0])
                    else:raise AssertionError('Unexpected reaction in short-call check: '+str(s['history'][-1]))
            else:
                for text in [OPTIONS['intro'][0],
                             'Ну чё сомневаться? В ВТБ в любом случае условия лучше. Кешбэк двадцать процентов на любые покупки без ограничений.',
                             'Все услуги бесплатные, уведомления тоже. Никаких лимитов нет.',
                             'Не знаю, посмотрите сами на сайте.', 'Ладно, до свидания.']:
                    if s['status']!='active':break
                    send(text)
            if s['status']!='ended':raise AssertionError('Call did not finish.')
            s=apply_evaluation(s,provider);packet=review_packet(s)
            visible=[c for c in packet['criteria'] if not c.get('hidden') and c['status']!='not_applicable']
            item.update(session=s,review=packet,visible_criteria=[c['id'] for c in visible])
            refusal=any(t.get('reaction') in {'decline','rude_decline','firm_refusal'} for t in s['history'])
            item['ok']=(packet['training_verdict'] in {'passed','retry'} and ('C07' in item['visible_criteria'])==refusal
                        and not any(c['status']=='requires_review' for c in packet['criteria']))
            if name=='short_positive':
                asked=[t['topic'] for t in item['turns'] if t.get('topic') in {'transfers','cash_withdrawal'}]
                item['ok'] &= s['end_reason']=='appointment_confirmed' and asked==['transfers','cash_withdrawal']
            else:item['ok'] &= packet['training_verdict']=='retry'
            print(json.dumps({'case':name,'ok':bool(item['ok']),'verdict':packet['training_verdict'],'rating':packet['rating']},ensure_ascii=False),flush=True)
        except Exception as exc:item.update(error=str(exc),error_type=type(exc).__name__,last_metrics=deepcopy(provider.metrics),session=s)
        save()
    report['status']='ok' if all(c['ok'] for c in report['cases']) else 'needs_review';save()
    print(json.dumps({'status':report['status'],'report':str(path)},ensure_ascii=False),flush=True)
    return 0 if report['status']=='ok' else 1

if __name__=='__main__':raise SystemExit(main())
