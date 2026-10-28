"""Порядок начала звонка закреплён в версии сценария каждой попытки."""
import re


GREETING = r"(?:здравствуйте|добрый\s+(?:день|вечер)|доброе\s+утро|доброго\s+(?:дня|вечера))"
TERMINAL_REACTIONS = {"closed", "firm_refusal", "rude_decline", "decline", "unanswered_goodbye"}


def employee_starts(scenario):
    return scenario.get("dialogue_features", {}).get("employee_first") is True


def starts_with_greeting(text):
    return bool(re.match(r"^(?:(?:да|алло)[\s,.!—–-]+)?" + GREETING + r"\b", text.strip(), re.I))


def first_client_response(history):
    return not any(turn["role"] == "client" for turn in history)


def greeting_required(scenario, history, reaction):
    return employee_starts(scenario) and first_client_response(history) and reaction not in TERMINAL_REACTIONS


def prepare_opening_reply(scenario, history, reaction, reply):
    if not greeting_required(scenario, history, reaction):
        return reply
    if reaction == "objection":
        return scenario["first_objection_reply"]
    employee = next((turn["text"] for turn in reversed(history) if turn["role"] == "employee"), "")
    if reaction == "clarify" and re.fullmatch(GREETING + r"[\s,.!…]*", employee.strip(), re.I):
        return "Здравствуйте. Слушаю вас."
    return reply if starts_with_greeting(reply) else "Здравствуйте. " + reply
