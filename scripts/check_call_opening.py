"""Проверка начала звонка. --live выполняет 8 запросов GigaChat на вымышленных репликах."""
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from do_vstrechi.data import load_bundle
from do_vstrechi.engine import advance, new_session
from do_vstrechi.conversation import naturalize_client_reply
from do_vstrechi.gigachat import GigaChatAnalyzer, load_local_environment
from do_vstrechi.opening import starts_with_greeting


CASES = [
    ('user_introduction', [
        ('здравствуйте я сотрудник втб у нас сегодня запланирована встреча. верно?', 'objection'),
        ('Давайте я кратко расскажу об условиях карты?', 'listen'),
    ]),
    ('greeting_then_introduction', [
        ('Здравствуйте!', 'clarify'),
        ('Я сотрудник ВТБ, у нас сегодня назначена встреча для получения карты. Всё верно?', 'objection'),
    ]),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.live:
        assert new_session(load_bundle())['history'] == []
        print('Employee-first initial state validated; no API calls.')
        return 0
    load_local_environment()
    provider = GigaChatAnalyzer()
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'synthetic': True,
              'scope': 'Two short complete input-analysis-controller-generation sequences; not a real employee study.',
              'conversations': [], 'status': 'running'}
    target = ROOT / 'var/call-opening-live-2026-10-25.json'
    for code, turns in CASES:
        session = new_session(load_bundle())
        session['client_reply_mode'] = 'natural'
        item = {'id': code, 'turns': []}
        report['conversations'].append(item)
        for index, (text, expected) in enumerate(turns):
            analysis = provider.analyze(session, text)
            analysis_metrics = deepcopy(provider.metrics)
            session = advance(session, text, analysis, mode='gigachat', usage=analysis_metrics.get('usage'))
            session = naturalize_client_reply(session, provider)
            client = session['history'][-1]
            row = {'employee': text, 'actions': analysis['actions'], 'client': client['text'],
                   'reaction': client['reaction'], 'reply_source': client['reply_source'],
                   'analysis_metrics': analysis_metrics, 'generation_metrics': deepcopy(session['calls'][-1]),
                   'client_outcome': session['client_outcome'],
                   'ok': client['reaction'] == expected and client['reply_source'] == 'generated'
                         and starts_with_greeting(client['text']) == (index == 0)
                         and session['client_outcome'] == 'undecided'}
            item['turns'].append(row)
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print(json.dumps({'id': code, 'turn': index + 1, 'ok': row['ok'], 'reply': row['client']}, ensure_ascii=False), flush=True)
    report['status'] = 'ok' if all(t['ok'] for c in report['conversations'] for t in c['turns']) else 'needs_review'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0 if report['status'] == 'ok' else 1


if __name__ == '__main__': raise SystemExit(main())
