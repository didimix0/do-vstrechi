"""Разговорная форма ответа не изменяет его разрешённый смысл."""
import re


QUESTION_FORMS={
    'service_scope':['А за что отдельно платить придётся?','Что бесплатно, а что нужно оплачивать?'],
    'benefit_relevance':['А зачем мне ещё одна карта?','И что мне даст вторая карта?'],
    'cashback_mechanics':['А как получать этот кешбэк? Категории выбирать нужно?','Что нужно сделать, чтобы получать кешбэк?'],
    'cashback_limit':['А сколько максимум вернётся за месяц?','У кешбэка есть месячный лимит?'],
    'transfers':['А с переводами в другие банки как — есть комиссия?','Переводить в другой банк можно без комиссии?'],
    'cash_withdrawal':['А наличные где можно снимать без комиссии?','Со снятием наличных в банкоматах какие условия?'],
}
REACTION_FORMS={
    'listen':['Да, расскажите. Только коротко.','Хорошо, слушаю. Что по условиям?'],
    'detail_answered':['А, понял. Спасибо.','Хорошо, с этим разобрались.','Теперь ясно, спасибо.'],
    'partial_answer_ack':['С этим понятнее. Но предыдущий вопрос пока остался.','Эту часть понял, но с прошлым вопросом ещё не разобрались.'],
    'deferred_detail_ack':['Понял, тогда это пока нужно уточнить.','Хорошо, с этим условием пока не разобрались.'],
    'unresolved_ack':['С этим так и не разобрались.','Этот вопрос пока остался без ответа.'],
    'source_question':['А где можно посмотреть эти условия?','Подскажите, где это можно проверить?'],
    'ask_time':['Хорошо, давайте встретимся. Во сколько вы приедете?','Давайте тогда встретимся. Напомните время?'],
    'ask_address':['А адрес напомните, пожалуйста?','По какому адресу мы договаривались?'],
    'details_confirmed':['Да, всё верно.','Всё правильно.'],
}


def conversational(scenario):
    return scenario.get('dialogue_features',{}).get('natural_dialogue') is True


def normalized(text):
    return re.sub(r'[^\w]+',' ',text.casefold()).strip()


def recent_client_replies(history):
    return [t['text'] for t in history if t['role']=='client'][-4:]


def form_examples(reaction,topic=None):
    return list(QUESTION_FORMS.get(topic,[]) if reaction=='product_question' else REACTION_FORMS.get(reaction,[]))


def language_context(session,context):
    recent=recent_client_replies(session['history'][:-1])
    forms=form_examples(context['reaction'],context.get('current_question',{}).get('id'))
    profile_form=session.get('client_profile',{}).get('replies',{}).get(context['reaction'])
    if profile_form:
        forms=[profile_form]+forms
    if context.get('opening_turn',{}).get('greeting_required') and context['reaction']=='objection' and session.get('client_profile'):
        forms=[session['client_profile']['opening']]
    return {'register':'обычная разговорная речь клиента, обращение на вы',
            'character':session.get('client_profile',{}).get('instruction','Спокойный, сдержанный разговор.'),
            'target':'Одна-две короткие фразы. Вопрос прямо по теме, без канцелярского вступления.',
            'recent_replies':recent,'examples':[s for s in forms if normalized(s) not in {normalized(t) for t in recent}][:2],
            'acknowledgement':'Короткое «понял» допустимо только о реально объяснённой части. Не изображай восторг или согласие с неизвестным условием.',
            'do_not_invent':['другой банк по названию','покупки клиента','работу или семью','тарифные значения','согласие на встречу вне разрешённой реакции']}


def alternative_reply(context):
    if not context.get('language_style') or context.get('continuation_reaction'):
        return None
    forms=form_examples(context['reaction'],context.get('current_question',{}).get('id'))
    recent={normalized(t) for t in context.get('language_style',{}).get('recent_replies',[])}
    return next((text for text in forms if normalized(text) not in recent),None)
