"""Проверка опубликованного режима на вымышленных данных. --live расходует API."""
import argparse
from datetime import datetime, timezone
import http.cookiejar
import json
from pathlib import Path
from urllib import request, error
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.live:
        print('Для проверки внешнего GigaChat и разбора добавьте --live.')
        return
    cfg = json.loads((ROOT / 'var/team-review/current.json').read_text(encoding='utf-8'))
    base = cfg['url']
    client = request.build_opener(request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def api(path, body=None, token=None, audio=None):
        headers = {}
        raw = None
        if body is not None or audio is not None:
            raw = audio if audio is not None else json.dumps(body, ensure_ascii=False).encode()
            headers = {'Content-Type': 'audio/wav' if audio is not None else 'application/json', 'Origin': base}
            if token:
                headers['X-Trainer-Token'] = token
        with client.open(request.Request(base + path, data=raw, headers=headers), timeout=200) as response:
            return json.load(response)

    api('/api/team/login', {'code': cfg['access_code']})
    bootstrap = api('/api/bootstrap')
    token = bootstrap['token']
    assert bootstrap['ai_ready'] and bootstrap['voice']['ready']
    assert api('/api/sessions') == []
    voice = api('/api/voice/transcribe', token=token,
                audio=(ROOT / 'var/voice-qa/greeting.wav').read_bytes())
    assert voice['text'] and voice['editable']
    print('Голос через HTTPS: Vosk распознал проверочную запись.', flush=True)
    session = api('/api/sessions', {'mode': 'gigachat', 'ending': 'appointment', 'character': 'calm'}, token)
    lines = [
        'Здравствуйте, Алексей. Меня зовут Дмитрий, я представитель ВТБ. Звоню по поводу назначенной на сегодня встречи для получения карты. Всё в силе?',
        'Понимаю. Можно я кратко расскажу об условиях, а вы решите, есть ли смысл встречаться?',
        'Выпуск и обслуживание самой карты бесплатные. Уведомления — отдельная услуга: 129 рублей в месяц при подключении; для отдельных уровней условия отличаются.',
        'Привычную карту можно оставить. Эту можно рассмотреть как дополнительную без платы за обслуживание. На встрече посмотрим, подходят ли вам её условия.',
        'Сохраняем нашу встречу, чтобы подробнее посмотреть условия?',
        'В ВТБ Онлайн переводы по номеру телефона через СБП в другие банки без комиссии до 300 тысяч рублей в месяц.',
        'В банкоматах ВТБ и банков Группы ВТБ снятие до одного миллиона рублей в месяц без комиссии. Для других банкоматов условия отличаются.',
        'Подтверждаем встречу сегодня в 18:00 по адресу: г. Учебный, ул. Примерная, д. 10. Всё верно?',
        'Подскажите, оригинал паспорта РФ будет у вас с собой на встрече?'
    ]
    for line in lines:
        if session['status'] != 'active':
            break
        session = api('/api/sessions/' + session['id'] + '/turns',
                      {'revision': session['revision'], 'request_id': uuid.uuid4().hex, 'text': line}, token)
        print('Реплика', session['turn_count'], '·', session['phase_label'], '·', session.get('client_reply_mode'), flush=True)
    if session['status'] == 'active':
        session = api('/api/sessions/' + session['id'] + '/finish',
                      {'revision': session['revision'], 'request_id': uuid.uuid4().hex}, token)
    assessed = api('/api/sessions/' + session['id'] + '/evaluate',
                   {'revision': session['revision'], 'request_id': uuid.uuid4().hex}, token)
    assert assessed['session']['rating'] is not None
    review = api('/api/sessions/' + session['id'] + '/review')
    assert review['history'] and review['criteria']
    assert len(api('/api/sessions')) == 1
    evidence = {'at': datetime.now(timezone.utc).isoformat(), 'synthetic': True,
                'external_https': True, 'url': base, 'login': 'ok',
                'scenario': bootstrap['exercise']['version'],
                'voice': {'engine': voice['engine'], 'editable': voice['editable'], 'text': voice['text']},
                'turns': session['turn_count'], 'outcome': session['outcome_label'],
                'rating': assessed['session']['rating'], 'criteria_count': len(review['criteria']),
                'history_and_report': 'ok'}
    (ROOT / 'docs/evidence/2026-10-26-team-link-live.json').write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
