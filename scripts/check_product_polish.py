"""Проверка выбранных характеров и полного звонка. --live расходует токены GigaChat."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from do_vstrechi.data import load_bundle
from do_vstrechi.engine import new_session,advance
from do_vstrechi.gigachat import GigaChatAnalyzer,load_local_environment
from do_vstrechi.conversation import naturalize_client_reply
from do_vstrechi.evaluation import apply_evaluation,session_rating


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--live',action='store_true');args=parser.parse_args()
    if not args.live:
        print('Для проверки с реальным API добавьте --live.');return
    load_local_environment();provider=GigaChatAnalyzer();bundle=load_bundle()
    report={'at':datetime.now(timezone.utc).isoformat(),'synthetic':True,'model':provider.model,'characters':[], 'full_call':[]}
    for character in ('calm','hurried','skeptical'):
        session=new_session(bundle,character=character);session['client_reply_mode']='natural'
        text='Здравствуйте, Алексей. Я Дмитрий, представитель ВТБ. У нас сегодня назначена встреча для получения карты. Всё в силе?'
        session=advance(session,text,{'actions':[{'code':'introduce','quote':text}]},mode='synthetic_probe')
        session=naturalize_client_reply(session,provider)
        report['characters'].append({'character':character,**session['history'][-1],'generation':dict(session['calls'][-1])})
        print(json.dumps(report['characters'][-1],ensure_ascii=False),flush=True)
    session=new_session(bundle);session['client_reply_mode']='natural'
    lines=[
        'Здравствуйте, Алексей. Меня зовут Дмитрий, я представитель ВТБ. Звоню по поводу назначенной на сегодня встречи для получения карты. Всё в силе?',
        'Понимаю. Можно я кратко расскажу об условиях, а вы решите, есть ли смысл встречаться?',
        'Выпуск и обслуживание самой карты бесплатные. Уведомления — отдельная услуга: 129 рублей в месяц при подключении; для отдельных уровней условия отличаются.',
        'Привычную карту можно оставить. Эту можно рассмотреть как дополнительную без платы за обслуживание. На встрече посмотрим, подходят ли вам её условия.',
        'Сохраняем нашу встречу, чтобы подробнее посмотреть условия?',
        'В ВТБ Онлайн переводы по номеру телефона через СБП в другие банки без комиссии до 300 тысяч рублей в месяц.',
        'Подробные условия снятия наличных предлагаю вместе посмотреть на встрече. Сейчас не буду обещать того, что нужно уточнить.',
        'Подтверждаем встречу сегодня в 18:00 по адресу: г. Учебный, ул. Примерная, д. 10. Всё верно?',
        'Подскажите, оригинал паспорта РФ будет у вас с собой на встрече?'
    ]
    for text in lines:
        if session['status']!='active':break
        analysis=provider.analyze(session,text)
        session=advance(session,text,analysis,mode='gigachat')
        session=naturalize_client_reply(session,provider)
        report['full_call'].append({'employee':text,'actions':[a['code'] for a in analysis['actions']],**session['history'][-1]})
        print(json.dumps(report['full_call'][-1],ensure_ascii=False),flush=True)
    report['outcome']=session['client_outcome'];report['status']=session['status']
    if session['status']=='ended':
        session=apply_evaluation(session,provider)
        report['rating']=session_rating(session);report['training_verdict']=session['training_verdict']
    report['all_generated']=all(t.get('reply_source')=='generated' for t in report['characters']+report['full_call'] if t.get('role')=='client' and not t.get('refusal_stage'))
    (ROOT/'var/product-polish-session.json').write_text(json.dumps(session,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'docs/evidence/2026-10-26-product-polish-live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'characters','full_call'}},ensure_ascii=False))


if __name__=='__main__':main()
