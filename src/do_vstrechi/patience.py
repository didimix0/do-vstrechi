"""Ограниченные уточнения: незакрытый вопрос не заставляет клиента ходить по кругу."""
from .questions import next_product_question, product_questions_enabled
from .detail_policy import detail_policy_enabled


def patience_enabled(scenario):
    features = scenario.get("dialogue_features", {})
    return product_questions_enabled(scenario) and features.get("question_patience") is True


def _record_stop(session, topic, employee_turn):
    entry = {"topic": topic, "employee_turn_id": employee_turn, "outcome": "unresolved"}
    events = session.setdefault("question_patience_events", [])
    if entry not in events:
        events.append(entry)


def _discard_question(session, question, client_turn):
    if question is None:
        return
    state = session["product_question_state"]
    record = next(r for r in state["records"] if r["question_id"] == question["id"])
    if client_turn in record["asked_turn_ids"]:
        record["asked_turn_ids"].remove(client_turn)
    if not record["asked_turn_ids"] and not record["answer_turn_ids"]:
        state["records"].remove(record)
        state["active_id"] = None


def _close(session, topic, employee_turn):
    _record_stop(session, topic, employee_turn)
    session.update(status="ended", phase="ended", end_reason="client_unresolved_questions", client_outcome="declined")
    session["flags"]["firm_refusal"] = True
    session["flags"]["meeting_ready"] = False
    return "unanswered_goodbye", None, None


def limit_questions(session, reaction, question, turn_id, codes):
    """Возвращает реакцию, выбранный вопрос и ID оставленной темы, если был переход."""
    scenario = session["materials"]["scenario"]
    if not patience_enabled(scenario):
        return reaction, question, None
    policy = scenario["question_policy"]
    maximum = policy["max_asks_per_topic"]
    client_turn, employee_turn = turn_id + "-client", turn_id + "-employee"
    state = session.get("product_question_state", {})
    blocking_statuses = {"unresolved"}
    if scenario.get("dialogue_features", {}).get("responsive_dialogue") is True:
        blocking_statuses.add("deferred")
    unresolved = [r for r in state.get("records", []) if r["status"] in blocking_statuses]
    meeting_attempt=('propose_next_step' in codes or detail_policy_enabled(scenario) and session['flags']['meeting_ready'])
    if (meeting_attempt and unresolved
            and reaction not in {"closed", "decline", "rude_decline", "firm_refusal"}):
        # Смена темы не означает, что существенный вопрос получил ответ.
        _discard_question(session, question, client_turn)
        return _close(session, unresolved[-1]["question_id"], employee_turn)
    if session["status"] != "active":
        return reaction, question, None
    if reaction == "product_question":
        record = next(r for r in state["records"] if r["question_id"] == question["id"])
        if len(record["asked_turn_ids"]) <= maximum:
            return reaction, question, None
        # next_product_question уже подготовил очередной вопрос. Его ещё не
        # произнесли: убираем эту запись, прежде чем переключиться/закончить.
        record["asked_turn_ids"].remove(client_turn)
        record.update(status="unresolved")
        record.setdefault("unanswered_turn_ids", []).append(employee_turn)
        state["active_id"] = None
        _record_stop(session, record["question_id"], employee_turn)
        unresolved = [r for r in state["records"] if r["status"] == "unresolved"]
        if len(unresolved) >= policy["max_unresolved_topics"]:
            return _close(session, record["question_id"], employee_turn)
        next_question = next_product_question(session, client_turn)
        if next_question:
            return "product_question", next_question, record["question_id"]
        return "unresolved_ack", None, record["question_id"]
    if reaction in {"source_question", "cashback_question", "missing_service", "missing_explanation", "clarify"}:
        # Считаем только реально произнесённые вопросы в текущей цепочке.
        previous = [t for t in session["history"] if t["role"] == "client" and t["id"] != "opening"]
        asks = 0
        for item in reversed(previous):
            if item.get("reaction") != reaction:
                break
            asks += 1
        if asks >= maximum or (reaction == "source_question" and "defer_product_detail" in codes):
            return _close(session, reaction, employee_turn)
    return reaction, question, None
