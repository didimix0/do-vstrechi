from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import uuid
from .contracts import validate_analysis
from .data import fingerprint, passport_question_enabled, validate_bundle
from .questions import (explanation_review, new_question_state, next_product_question,
                        product_questions_enabled, update_explanation)
from .patience import limit_questions
from .opening import employee_starts, prepare_opening_reply
from .benefits import grounded_benefit_dialogue
from .followups import activate_followups,has_topic_detail
from .refusal import refusal_reply
from .characters import selected_profile, profile_reply
from .character_behavior import attach_behavior


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_session(bundle: dict, ending: str = "appointment", character: str = "calm") -> dict:
    validate_bundle(bundle)
    if ending not in {"appointment", "decline", "rude_decline", "hangup"}:
        raise ValueError("Неизвестный вариант решения клиента.")
    profile = selected_profile(bundle['scenario'], character)
    if character != 'calm' and profile is None:
        raise ValueError('Неизвестный характер клиента.')
    return {
        "id": str(uuid.uuid4()), "revision": 0, "created_at": now(),
        "status": "active", "phase": "call_opening", "end_reason": None,
        "client_outcome": "undecided", "training_verdict": "not_evaluated",
        "ending_preference": ending, "turn_count": 0,
        "materials": deepcopy(bundle),
        **({'client_profile': profile} if profile else {}),
        "material_hashes": {k: fingerprint(v) for k, v in bundle.items()},
        "flags": {"introduced": False, "brief_accepted": False, "reason_known": False,
                  "needs_known": False, "service_discussed": False, "cashback_discussed": False,
                  "pending_service_question": False, "pending_product_clarification": False,
                  "meeting_ready": False, "time_discussed": False, "address_discussed": False,
                  "firm_refusal": False,
                  **({"passport_question_detected": False, "passport_answered": False}
                     if passport_question_enabled(bundle["scenario"]) else {})},
        "history": ([] if employee_starts(bundle["scenario"]) else
                    [{"id": "opening", "role": "client", "text": bundle["scenario"]["opening"]}]),
        "observations": [], "calls": [],
        **({"product_question_state": new_question_state()} if product_questions_enabled(bundle["scenario"]) else {}),
    }


def advance(session: dict, employee_text: str, analysis: dict, *, mode: str,
            usage: dict | None = None, elapsed_seconds: float = 0) -> dict:
    if session["status"] != "active":
        raise ValueError("Звонок уже завершён.")
    if not isinstance(employee_text, str) or not employee_text.strip() or len(employee_text) > 4000:
        raise ValueError("Введите непустую реплику длиной до 4000 символов.")
    actions = validate_analysis(analysis, employee_text)
    result = deepcopy(session)
    result["client_reply_notice"] = ""
    result["turn_count"] += 1
    turn_id = f"t{result['turn_count']:02d}"
    result["history"].append({"id": turn_id + "-employee", "role": "employee", "text": employee_text})
    for action in actions:
        result["observations"].append({**asdict(action), "turn_id": turn_id + "-employee",
                                       "verification": "requires_review", "mode": mode})
    result["calls"].append({"mode": mode, "role": "action_analysis", "usage": usage or {},
                            "elapsed_seconds": elapsed_seconds, "at": now()})
    codes = {a.code for a in actions}
    flags = result["flags"]
    replies = result["materials"]["scenario"]["replies"]
    passport_enabled = passport_question_enabled(result["materials"]["scenario"])
    passport_question = passport_enabled and "ask_passport_availability" in codes
    passport_reminder = passport_enabled and "remind_passport" in codes
    if passport_enabled:
        flags["passport_question_detected"] = flags.get("passport_question_detected", False) or passport_question
        flags.setdefault("passport_answered", False)
    reaction = "clarify"
    client_event = None
    continuation = None
    question = None
    refusal_step = None
    explanation_enabled = product_questions_enabled(result["materials"]["scenario"])
    responsive = explanation_enabled and result["materials"]["scenario"].get("dialogue_features", {}).get("responsive_dialogue") is True
    if explanation_enabled and 'explain_current_detail' in codes:
        for detail_code in ('explain_transfers','explain_withdrawals'):
            if has_topic_detail(detail_code,employee_text):codes.add(detail_code)
    if explanation_enabled and codes & {'explain_transfers','explain_withdrawals'}:
        codes.add('explain_service')
    if (responsive and not flags["introduced"] and "introduce" in codes
            and not codes & {"possible_product_error", "pressure", "end_meeting", "respect_refusal"}):
        # Вступление может содержать условия: первое возражение не стирает сказанное.
        if "explain_service_scope" in codes:
            codes.add("explain_service")
        if codes & {"explain_cashback_mechanics", "explain_cashback_limits"}:
            codes.add("explain_cashback")
        if "explain_service" in codes:
            flags["service_discussed"] = True
        if "explain_cashback" in codes:
            flags["cashback_discussed"] = True
            flags["pending_service_question"] = not flags["service_discussed"]
        update_explanation(result, codes, turn_id + "-employee")

    if codes & {"end_meeting", "respect_refusal"}:
        reaction = "closed"
        result["status"] = "ended"
        result["end_reason"] = "client_declined" if flags["firm_refusal"] else "employee_ended"
    elif "pressure" in codes or flags["firm_refusal"]:
        reaction = "firm_refusal"
        refusal_step = refusal_reply(result)
        flags["firm_refusal"] = True
        flags['meeting_ready'] = False
        result["phase"] = "final_refusal"
        result["client_outcome"] = "declined"
        if refusal_step['end_call']:
            result.update(status='ended',end_reason='client_declined')
    elif "possible_product_error" in codes:
        flags["pending_product_clarification"] = True
        reaction = "source_question"
    elif not flags["introduced"]:
        if "introduce" in codes:
            flags["introduced"] = True
            result["phase"] = "objection"
            reaction = "objection"
    else:
        if explanation_enabled:
            if "explain_service_scope" in codes:
                codes.add("explain_service")
            if codes & {"explain_cashback_mechanics", "explain_cashback_limits"}:
                codes.add("explain_cashback")
        corrected = "clarify_product_terms" in codes and bool(codes & {"explain_service", "explain_cashback"})
        if corrected:
            flags["pending_product_clarification"] = False
        disclosures = []
        if "ask_reason" in codes:
            flags["reason_known"] = True
            disclosures.append("reason")
        if "ask_needs" in codes:
            flags["needs_known"] = True
            disclosures.append("needs")
        if "explain_service" in codes:
            flags["service_discussed"] = True
            flags["pending_service_question"] = False
        if "explain_cashback" in codes:
            flags["cashback_discussed"] = True
            if not flags["service_discussed"]:
                flags["pending_service_question"] = True
        if "offer_brief_explanation" in codes:
            flags["brief_accepted"] = True
        deferred_detail = False
        if explanation_enabled and not flags["pending_product_clarification"]:
            activate_followups(result, codes)
            deferred_detail = update_explanation(result, codes, turn_id + "-employee")
            if not flags["pending_service_question"] and not (responsive and disclosures):
                question = next_product_question(result, turn_id + "-client")
        confirming = flags["meeting_ready"] and bool(codes & {"confirm_time", "confirm_address"})
        if flags["pending_product_clarification"]:
            reaction = "source_question"
        elif responsive and disclosures:
            # Отвечаем сотруднику, сохраняя собственный открытый вопрос в памяти.
            # Непроизнесённый повтор вопроса не расходует лимит уточнений.
            result["phase"] = "clarification"
            reaction = "reason_and_needs" if len(disclosures) > 1 else disclosures[0]
        elif question:
            reaction = "product_question"
            result["phase"] = "explanation"
        elif ("propose_next_step" in codes or confirming
              or (explanation_enabled and result['product_question_state'].get('meeting_offer_pending'))):
            if explanation_enabled:
                result['product_question_state']['meeting_offer_pending'] = False
            if not (flags["service_discussed"] or flags["cashback_discussed"]):
                reaction = "missing_explanation"
            elif flags["pending_service_question"]:
                reaction = "missing_service"
            else:
                reaction = result["ending_preference"]
                if reaction == "appointment":
                    flags["meeting_ready"] = True
                    flags["time_discussed"] |= "confirm_time" in codes
                    flags["address_discussed"] |= "confirm_address" in codes
                    result["phase"] = "confirmation"
                    result["client_outcome"] = "ready_to_meet"
                    if not flags["time_discussed"]:
                        reaction = "ask_time"
                    elif not flags["address_discussed"]:
                        reaction = "ask_address"
                    elif passport_enabled and not flags["passport_answered"]:
                        # Клиент подтверждает реквизиты, но не подсказывает пропущенный вопрос.
                        reaction = "details_confirmed"
                        result["client_outcome"] = "appointment_agreed"
                    else:
                        reaction = "appointment"
                        result["status"] = "ended"
                        result["end_reason"] = "appointment_confirmed"
                        result["client_outcome"] = "appointment_agreed"
                elif reaction == "hangup":
                    client_event = "client_hangup"
                    result["status"] = "ended"
                    result["end_reason"] = "client_hangup"
                    result["client_outcome"] = "call_ended_by_client"
                else:
                    flags["firm_refusal"] = True
                    result["phase"] = "final_refusal"
                    result["client_outcome"] = "declined"
        elif deferred_detail:
            accepted=any(r.get('deferral_policy_accepted') and turn_id+'-employee' in r['answer_turn_ids'] for r in result.get('product_question_state',{}).get('records',[]))
            reaction = 'meeting_detail_ack' if accepted else "deferred_detail_ack"
        elif explanation_enabled and codes & {"explain_service", "explain_cashback", "connect_benefit",'explain_current_detail'} and not flags["pending_service_question"]:
            remaining = any(r['status'] in {'deferred', 'unresolved'} for r in result['product_question_state']['records'])
            reaction = "partial_answer_ack" if grounded_benefit_dialogue(result['materials']['scenario']) and remaining else "detail_answered"
            result["phase"] = "explanation"
        elif codes & {"explain_service", "explain_cashback"}:
            result["phase"] = "explanation"
            if flags["pending_service_question"]:
                reaction = "cashback_question"
            elif flags["cashback_discussed"]:
                reaction = "ready_to_schedule"
            else:
                reaction = "service_explained"
        elif disclosures:
            result["phase"] = "clarification"
            reaction = "reason_and_needs" if len(disclosures) > 1 else disclosures[0]
        elif "offer_brief_explanation" in codes:
            result["phase"] = "listening"
            reaction = "listen"
        elif "introduce" in codes:
            reaction = "objection"
            result["phase"] = "objection"
    if ((passport_question or passport_reminder) and flags["introduced"] and not flags["firm_refusal"]
            and not flags["pending_product_clarification"] and not flags["pending_service_question"]
            and "possible_product_error" not in codes and reaction != "product_question" and client_event is None):
        # Ответ на вопрос не является согласием получить карту и не закрывает другие вопросы.
        flags["passport_answered"] |= passport_question
        if flags["meeting_ready"] and flags["time_discussed"] and flags["address_discussed"] and flags["passport_answered"]:
            reaction = "passport_appointment"
            result["status"] = "ended"
            result["end_reason"] = "appointment_confirmed"
            result["client_outcome"] = "appointment_agreed"
        elif result["status"] == "ended":
            reaction = "passport_available"
        elif flags["meeting_ready"]:
            reaction = "passport_available"
            if not flags["time_discussed"]:
                continuation = "ask_time"
            elif not flags["address_discussed"]:
                continuation = "ask_address"
        else:
            reaction = "passport_objection"
    switched_from = None
    if client_event is None:
        reaction, question, switched_from = limit_questions(result, reaction, question, turn_id, codes)
        if result["end_reason"] == "client_unresolved_questions":
            continuation = None
    if client_event:
        result["history"].append({"id": turn_id + "-event", "role": "event", "type": client_event,
                                  "text": result["materials"]["scenario"]["event_descriptions"][client_event]})
    else:
        if reaction == "product_question":
            reply = question["text"]
        else:
            reply = " ".join(replies[key] for key in ("reason", "needs")) if reaction == "reason_and_needs" else replies[reaction]
        if continuation:
            reply += " " + replies[continuation]
        if not continuation and reaction != 'product_question':
            reply = profile_reply(result, reaction, reply)
        if refusal_step:
            reply = refusal_step['text']
        reply = prepare_opening_reply(result["materials"]["scenario"], result["history"], reaction, reply)
        if reaction == 'objection' and result.get('client_profile') and not any(t['role']=='client' for t in result['history']):
            reply = result['client_profile']['opening']
        result["history"].append({"id": turn_id + "-client", "role": "client", "text": reply,
                                  "reaction": reaction, "reply_source": "scripted"})
        if refusal_step:
            result['history'][-1].update(refusal_stage=refusal_step['stage'],refusal_continuations=refusal_step['continuations'])
        if continuation:
            result["history"][-1]["continuation_reaction"] = continuation
        if question:
            result["history"][-1]["product_question_id"] = question["id"]
        if switched_from:
            result["history"][-1]["switched_from_question_id"] = switched_from
    if result["turn_count"] >= result["materials"]["scenario"]["max_turns"] and result["status"] == "active":
        result["status"] = "ended"
        result["end_reason"] = "turn_limit"
    if result["status"] == "ended":
        result["phase"] = "ended"
    attach_behavior(result,employee_text)
    return result


def review_packet(session: dict) -> dict:
    """Свидетельства и, если запрошена, отдельная проверенная оценка модели."""
    rubric = session["materials"]["rubric"]
    packet = {
        "session_id": session["id"], "training_verdict": "not_evaluated",
        "evaluation_status": "requires_review", "automatic_evaluator_implemented": True,
        "client_outcome": session["client_outcome"], "end_reason": session["end_reason"],
        "channel": session["materials"]["scenario"]["channel"],
        "dialogue_complete": session["status"] == "ended",
        "evidence_incomplete": session["end_reason"] in {"client_hangup", "turn_limit", "employee_stopped"},
        "appointment_evidence": {
            "time_discussed": session["flags"]["time_discussed"],
            "address_discussed": session["flags"]["address_discussed"],
            "verification": "requires_review",
        },
        "passport_evidence": {
            "enabled": passport_question_enabled(session["materials"]["scenario"]),
            "question_detected": session["flags"].get("passport_question_detected", False),
            "client_answer_recorded": session["flags"].get("passport_answered", False),
            "verification": "requires_review",
        },
        "material_hashes": session["material_hashes"],
        "explanation_evidence": explanation_review(session),
        "unanswered_question_events": deepcopy(session.get("question_patience_events", [])),
        "criteria": [{"id": criterion["id"], "title": criterion["title"],
                      **({"completion_rule": criterion["completion_rule"]} if criterion.get("completion_rule") else {}),
                      "status": "requires_review", "fact_ids": criterion["fact_ids"],
                      "applies_when": criterion["applies_when"],
                      "evidence_candidates": ([{"turn_id": t["id"], "quote": t["text"], "verification": "requires_review"}
                                               for t in session["history"] if t["role"] == "employee"]
                                              if criterion["id"] == "C10" else
                                              [deepcopy(o) for o in session["observations"]
                                               if o["code"] in criterion["candidate_actions"]])}
                     for criterion in rubric["criteria"]],
        "notice": "Автоматический зачёт не выставлен. Нужны сверка условий, проверка смысловой оценки и применимости критериев. При сбросе нельзя требовать реплики, которые сотрудник уже не мог произнести.",
    }
    from .evaluation import fact_sources, input_hash, session_rating
    refusal_seen = any(t.get('reaction') in {'decline','rude_decline','firm_refusal'} for t in session['history'])
    for criterion in packet['criteria']:
        if criterion['id']=='C07' and not refusal_seen:
            criterion['hidden'] = True
    evaluation = session.get("evaluation")
    packet["evaluation_error"] = session.get("evaluation_error", "")
    if evaluation and evaluation.get("input_hash") == input_hash(session):
        packet.update(training_verdict=evaluation["training_verdict"], evaluation_status="completed",
                      rating=session_rating(session),
                      notice=evaluation["notice"], evaluation_version=evaluation["version"],
                      evaluation_warnings=deepcopy(evaluation["warnings"]),
                      coverage_complete=evaluation["coverage_complete"], coverage_note=evaluation["coverage_note"])
        assessments = {c["id"]: c for c in evaluation["criteria"]}
        for criterion in packet["criteria"]:
            criterion.update(deepcopy(assessments[criterion["id"]]))
            if criterion['status']=='not_applicable':
                criterion['hidden'] = True
            if criterion["id"] == "C09" and criterion["status"] == "not_met":
                criterion["next_step"] = "Перед завершением звонка спросите, будет ли у клиента при себе оригинал паспорта РФ на встрече."
        packet["claims"] = deepcopy(evaluation["claims"])
        if "speech_review" in evaluation:
            packet["speech_review"] = deepcopy(evaluation["speech_review"])
        for claim in packet["claims"]:
            claim["facts"] = [fact_sources(session, ref) for ref in claim["facts"]]
    return packet

