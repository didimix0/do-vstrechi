"""Окончательный отказ: одно напоминание, затем завершение без зацикливания."""

FINAL_REFUSALS = {'decline', 'rude_decline', 'firm_refusal'}
DEFAULT_REMINDER = 'Я уже отказался от карты. Пожалуйста, отмените встречу, больше рассказывать не нужно.'
DEFAULT_CLOSING = 'Я не буду получать карту. На этом закончим. До свидания.'


def refusal_reply(session):
    history = session['history']
    first = next((i for i,turn in enumerate(history)
                  if turn['role']=='client' and turn.get('reaction') in FINAL_REFUSALS), None)
    if first is None:
        return {'stage':'initial', 'continuations':0,
                'text':session['materials']['scenario']['replies']['firm_refusal'], 'end_call':False}
    # Счётчик выводится из истории: он работает и в уже начатых попытках,
    # в которых до исправления не сохранялось отдельное состояние отказа.
    count = sum(t['role']=='employee' for t in history[first+1:])
    policy = session['materials']['scenario'].get('refusal_policy', {})
    if count <= 1:
        return {'stage':'reminder', 'continuations':count,
                'text':policy.get('reminder',DEFAULT_REMINDER), 'end_call':False}
    return {'stage':'ended', 'continuations':count,
            'text':policy.get('closing',DEFAULT_CLOSING), 'end_call':True}
