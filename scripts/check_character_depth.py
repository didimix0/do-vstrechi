"""--live: один звонок недоверчивому клиенту и короткий пример спешащего."""
import argparse,json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from do_vstrechi.data import load_bundle
from do_vstrechi.engine import new_session,advance,review_packet
from do_vstrechi.gigachat import GigaChatAnalyzer,load_local_environment
from do_vstrechi.conversation import naturalize_client_reply
from do_vstrechi.evaluation import apply_evaluation,session_rating
from do_vstrechi.webapp import OPTIONS


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--live',action='store_true');args=parser.parse_args()
    if not args.live:print('Для проверки через GigaChat добавьте --live.');return
    load_local_environment();provider=GigaChatAnalyzer();bundle=load_bundle()
    report={'at':datetime.now(timezone.utc).isoformat(),'synthetic':True,'model':provider.model,'skeptical':[],'hurried':[]}
    s=new_session(bundle,character='skeptical');s['client_reply_mode']='natural'
    cashback=('Кешбэк начисляется рублями за подходящие покупки в выбранных категориях. Категории выбираются каждый месяц в ВТБ Онлайн. '
              'В предложении указано до 15 процентов в отдельных категориях, а не на любые покупки. Общий предел — 3000 рублей в месяц, это максимум, а не гарантированная сумма. '
              'Если вам доступен пробный период для новых клиентов, можно выбрать пять категорий. После него число зависит от уровня программы банка.')
    lines=[OPTIONS[k][0] for k in ('intro','offer','service','benefit')]+[cashback]+[OPTIONS[k][0] for k in ('meeting','transfers','withdrawals','clarify_later','withdrawal_limits','both','passport')]
    try:
        for text in lines:
            if s['status']!='active':break
            text=text.format(**s['materials']['scenario']['appointment'])
            analysis=provider.analyze(s,text);s=advance(s,text,analysis,mode='gigachat');s=naturalize_client_reply(s,provider)
            item={'employee':text,'actions':[a['code'] for a in analysis['actions']],**s['history'][-1]}
            report['skeptical'].append(item);print(json.dumps(item,ensure_ascii=False),flush=True)
        report['skeptical_outcome']=s['client_outcome'];report['skeptical_status']=s['status']
        report['accepted_deferrals']=s.get('product_question_state',{}).get('accepted_deferrals',0)
        if s['status']=='ended':
            s=apply_evaluation(s,provider);report['rating']=session_rating(s)
            report['C04']=next(c for c in review_packet(s)['criteria'] if c['id']=='C04')
        h=new_session(bundle,character='hurried');h['client_reply_mode']='natural'
        for key in ('intro','offer','service','benefit'):
            text,codes=OPTIONS[key]
            if key=='service':text=text+' '+cashback;codes=['explain_service_scope','explain_cashback','explain_cashback_mechanics','explain_cashback_limits']
            h=advance(h,text,{'actions':[{'code':c,'quote':text} for c in codes]},mode='synthetic_probe')
            h=naturalize_client_reply(h,provider);report['hurried'].append({'employee':text,**h['history'][-1]})
        report['hurry_reminders']=h.get('character_state',{}).get('time_reminders',0)
    finally:
        (ROOT/'docs/evidence/2026-10-26-character-depth-live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (ROOT/'var/character-depth-session.json').write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'skeptical','hurried','C04'}},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
