"""Повтор пользовательского разговора. --live расходует API; --evaluate добавляет разбор."""
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from do_vstrechi.data import load_bundle
from do_vstrechi.engine import advance, new_session, review_packet
from do_vstrechi.conversation import naturalize_client_reply
from do_vstrechi.evaluation import apply_evaluation
from do_vstrechi.gigachat import GigaChatAnalyzer, load_local_environment


class Capture(GigaChatAnalyzer):
    last_output = None
    def _structured(self, *args, **kwargs):
        value = super()._structured(*args, **kwargs)
        self.last_output = deepcopy(value)
        return value


def cases():
    source = json.loads((ROOT / 'scripts/cases/reported_benefit_loop.v1.json').read_text(encoding='utf-8'))
    exact = [t['text'] for t in source['turns']]
    exact += ['Встречаемся сегодня в 18:00 по адресу: город Учебный, улица Примерная, дом 10. Всё верно?',
              'Подскажите, оригинал паспорта РФ будет у вас с собой на встрече?']
    negative = [exact[0], exact[3], 'Условия очень выгодные, много кешбэка.',
                'Просто хорошая карта, остальное посмотрите на сайте.', 'Сохраняем встречу?']
    return [('exact_report', exact), ('vague_praise', negative)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--evaluate', action='store_true')
    parser.add_argument('--case', choices=['exact_report','vague_praise'])
    args = parser.parse_args()
    if not args.live:
        print('Run tests/test_benefit_dialogue.py for offline replay. No network used.')
        return 0
    load_local_environment()
    provider = Capture()
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'synthetic': True,
              'scope': 'Full action analysis, controller and client generation for one reported conversation and a negative case; not a user study.',
              'status': 'running', 'cases': []}
    path = ROOT / 'var/benefit-replay-live-2026-10-25.json'
    def save(): path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for name, turns in cases():
        if args.case and args.case != name: continue
        session = new_session(load_bundle())
        session['client_reply_mode'] = 'natural'
        item = {'id':name, 'turns':[], 'ok':False}
        report['cases'].append(item)
        try:
            for text in turns:
                if session['status'] != 'active': break
                analysis = provider.analyze(session, text)
                analysis_metrics = deepcopy(provider.metrics)
                session = advance(session, text, analysis, mode='gigachat', usage=analysis_metrics.get('usage'))
                session = naturalize_client_reply(session, provider)
                last = session['history'][-1]
                row = {'employee':text, 'actions':analysis['actions'], 'client':last.get('text'),
                       'reaction':last.get('reaction'), 'reply_source':last.get('reply_source'),
                       'client_outcome':session['client_outcome'],
                       'open_questions':[{'id':r['question_id'],'status':r['status']} for r in session['product_question_state']['records'] if r['status'] in {'open','deferred','unresolved'}],
                       'analysis_metrics':analysis_metrics, 'last_metrics':deepcopy(session['calls'][-1])}
                item['turns'].append(row)
                save()
                print(json.dumps({'case':name,'turn':session['turn_count'],'reaction':row['reaction'],
                                  'source':row['reply_source'],'client':row['client']}, ensure_ascii=False), flush=True)
            item.update(end_reason=session['end_reason'], client_outcome=session['client_outcome'],
                        question_state=session['product_question_state'], errors=[c for c in session['calls'] if c.get('status')=='error'])
            if name == 'exact_report':
                benefit = next(r for r in session['product_question_state']['records'] if r['question_id']=='benefit_relevance')
                item['ok'] = (session['end_reason']=='appointment_confirmed' and benefit['status']=='answer_detected'
                              and len(benefit['asked_turn_ids']) <= 2 and not item['errors'])
            else:
                item['ok'] = (session['client_outcome']=='declined' and session['end_reason']=='client_unresolved_questions'
                              and 'benefit_relevance' not in session['product_question_state']['covered'] and not item['errors'])
            if args.evaluate and name == 'exact_report' and session['status']=='ended':
                session = apply_evaluation(session, provider)
                item['evaluation'] = review_packet(session)
                item['evaluation_metrics'] = deepcopy(provider.metrics)
            item['session'] = session
        except Exception as exc:
            item.update(error=str(exc), error_type=type(exc).__name__, last_metrics=deepcopy(provider.metrics), raw_output=provider.last_output)
        save()
    report['status'] = 'ok' if all(c['ok'] and not c.get('error') for c in report['cases']) else 'needs_review'
    save()
    print(json.dumps({'status':report['status'],'report':str(path)}, ensure_ascii=False), flush=True)
    return 0 if report['status']=='ok' else 1


if __name__=='__main__': raise SystemExit(main())
