"""Формулирование выбранной контроллером реакции; состояние сессии не меняется моделью."""
from copy import deepcopy
from .contracts import ContractError
from .client import client_context, validate_client_reply
from .engine import now
from .gigachat import ProviderError
from .client_language import alternative_reply
from .character_behavior import enforce_time_style


def naturalize_client_reply(session: dict, provider) -> dict:
    if session.get("client_reply_mode") != "natural" or not session["history"] or session["history"][-1]["role"] != "client":
        return session
    result = deepcopy(session)
    result["client_reply_notice"] = ""
    last = result["history"][-1]
    if last.get('character_cue'):
        # Обязательные напоминания различаются и не зависят от послушности LLM.
        last['reply_source']='scripted_character_behavior'
        return result
    stable_endings = result['materials']['scenario'].get('dialogue_features', {}).get('stable_endings') is True
    if (last.get('refusal_stage') in {'initial','reminder','ended'}
            or stable_endings and last.get('reaction') in {'closed', 'decline', 'rude_decline', 'firm_refusal', 'unanswered_goodbye'}):
        # Решение уже принял контроллер: короткое завершение не требует генерации.
        last['reply_source'] = 'scripted_ending'
        return result
    try:
        # Не даём провайдеру изменять рабочую сессию даже при ошибке адаптера.
        text = provider.generate_client_reply(deepcopy(result))
        text,style_guard=enforce_time_style(result,text)
        validate_client_reply({"reaction": last["reaction"], "text": text}, client_context(result))
    except (ProviderError, ContractError) as exc:
        context=client_context(result)
        alternative=alternative_reply(context)
        if alternative:
            try:
                validate_client_reply({'reaction':last['reaction'],'text':alternative},context)
            except ContractError:
                pass
            else:
                last['text']=alternative
        last["reply_source"] = "scripted_fallback"
        next_step = 'можно продолжить разговор.' if result['status'] == 'active' else 'можно перейти к разбору.'
        result["client_reply_notice"] = "Не удалось подготовить ответ клиента. Использована готовая фраза сценария; " + next_step
        call = deepcopy(provider.metrics)
        call.update(role="client_generation", status="error", error_type=type(exc).__name__, fallback="scripted")
        if isinstance(exc, ContractError):
            call["validation_detail"] = str(exc)
    else:
        last["text"] = text
        last["reply_source"] = 'scripted_character_style' if style_guard else "generated"
        call = deepcopy(provider.metrics)
        call.update(role="client_generation", status="ok",character_style_guard=style_guard)
    call["at"] = now()
    result["calls"].append(call)
    return result
