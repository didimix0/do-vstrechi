"""Заметные особенности персонажа с ограниченным числом речевых сигналов."""
import re
from .opening import GREETING

TIME_CUES = [
    'Я сейчас ненадолго на связи.',
    'Давайте только самое важное, пожалуйста — времени немного.',
    'Мне скоро нужно заканчивать разговор, давайте самое важное.',
]
LONG_TIME_CUES = [
    'Извините, давайте покороче — у меня сейчас мало времени.',
    'Давайте самое главное — долго обсуждать сейчас не получится.',
]
LONG_CUES = [
    'Пожалуйста, по одному главному условию. В длинном объяснении я теряю нить.',
    'Давайте короче, пожалуйста. Выделите самое важное.',
]
TERMINAL = {'closed','decline','rude_decline','firm_refusal','unanswered_goodbye','appointment','passport_appointment'}
TERMINAL |= {'details_confirmed','passport_available','passport_objection'}


def behavior_enabled(session):
    return session['materials']['scenario'].get('dialogue_features',{}).get('distinct_client_behavior') is True


def with_cue(text, cue):
    if cue in text:
        return text
    greeting = re.match(r'^('+GREETING+r')[\s,.!—–-]*',text,re.I)
    if greeting:
        return greeting[1]+'. '+cue+' '+text[greeting.end():]
    return cue+' '+text


def attach_behavior(session, employee_text):
    if not behavior_enabled(session) or session.get('client_profile',{}).get('id')!='hurried':
        return
    last=session['history'][-1]
    if last['role']!='client' or last.get('reaction') in TERMINAL or session['status']!='active' or session['flags']['firm_refusal']:
        return
    state=session.setdefault('character_state',{'time_reminders':0,'long_reactions':0,'last_cue_turn':-10,'used_cues':[]})
    turn=session['turn_count'];cue=None;kind=None
    long=len(employee_text)>420 or len(employee_text.split())>65
    if not state['time_reminders']:
        cue=TIME_CUES[0];kind='time_reminder'
    elif long and state['long_reactions']<2:
        pool=LONG_TIME_CUES if state['time_reminders']<2 and turn-state['last_cue_turn']>=2 else LONG_CUES
        cue=next((c for c in pool if c not in state['used_cues']),None)
        kind='long_answer_with_time' if pool is LONG_TIME_CUES else 'long_answer'
    elif state['time_reminders']<2 and turn>=4 and turn-state['last_cue_turn']>=2:
        cue=next((c for c in TIME_CUES[1:] if c not in state['used_cues']),None);kind='time_reminder'
    if cue:
        if kind in {'time_reminder','long_answer_with_time'}:state['time_reminders']+=1
        if kind in {'long_answer','long_answer_with_time'}:state['long_reactions']+=1
        state['last_cue_turn']=turn;state['used_cues'].append(cue)
        last['character_cue']={'kind':kind,'text':cue,'long_employee_answer':long}
        last['text']=(last['text']+' '+cue if kind=='time_reminder' and last.get('reaction')=='detail_answered'
                      else with_cue(last['text'],cue))


def enforce_time_style(session,text):
    if (behavior_enabled(session) and session.get('client_profile',{}).get('id')=='hurried'
            and re.search(r'времен[иь].{0,20}(?:мало|немного|нет|огранич)|(?:мало|немного|нет)\s+времени|ненадолго|некогда|я\s+(?:спешу|тороплюсь)|долго.{0,20}(?:не\s+могу|не\s+получится)',text,re.I)):
        # Это ограничение манеры, не ошибка API: сохраняем выбранный смысл
        # и не даём генератору добавлять напоминания сверх программы.
        return session['history'][-1]['text'],True
    return text,False
