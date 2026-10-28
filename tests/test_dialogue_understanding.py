from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from do_vstrechi.client import client_context, validate_client_reply
from do_vstrechi.contracts import bind_action_evidence
from legacy_fixtures import load_bundle
from do_vstrechi.engine import advance, new_session, review_packet


def step(session, text, *codes):
    return advance(session, text, {"actions": [{"code": c, "quote": text} for c in codes]}, mode="test")


class DialogueUnderstandingTests(unittest.TestCase):
    def setUp(self):
        self.bundle = load_bundle()
        self.session = step(new_session(self.bundle), "Здравствуйте, я представитель ВТБ, звоню о встрече для получения карты.", "introduce")

    def open_cost(self):
        return step(self.session, "Карта бесплатная.", "explain_service")

    def deferred_cost(self):
        session = step(self.open_cost(), "Не знаю. Привычную карту можно оставить и рассмотреть эту как дополнительную без платы за обслуживание.", "defer_product_detail", "connect_benefit")
        self.assertEqual(session["product_question_state"]["records"][0]["status"], "deferred")
        return session

    def test_employee_question_gets_answer_and_does_not_consume_unspoken_repeat(self):
        session = step(self.open_cost(), "А почему вы не хотите получать вторую карту?", "ask_reason")
        self.assertEqual(session["history"][-1]["reaction"], "reason")
        state = session["product_question_state"]
        self.assertEqual(state["active_id"], "service_scope")
        self.assertEqual(state["records"][0]["asked_turn_ids"], ["t02-client"])
        self.assertEqual(state["records"][0]["status"], "open")
        context = client_context(session)
        self.assertTrue(context["conversation_memory"]["open_questions"])
        session = step(session, "Выпуск и обслуживание бесплатны, уведомления отдельно стоят 129 рублей в месяц при подключении.", "explain_service_scope")
        self.assertEqual(session["product_question_state"]["records"][0]["status"], "answer_detected")
        self.assertEqual(session["history"][-1]["product_question_id"], "benefit_relevance")

    def test_needs_question_is_answered_without_inventing_purchase_preferences(self):
        session = step(self.open_cost(), "Что для вас важно в банковской карте?", "ask_needs")
        self.assertEqual(session["history"][-1]["reaction"], "needs")
        self.assertEqual(len(session["product_question_state"]["records"][0]["asked_turn_ids"]), 1)
        self.assertEqual(client_context(session)["persona"]["existing_bank"], "не указан")

    def test_combined_question_and_explanation_keep_both_meanings(self):
        session = step(self.open_cost(), "Выпуск и обслуживание бесплатны, а подключаемые уведомления оплачиваются отдельно. Что для вас важно в карте?", "explain_service_scope", "ask_needs")
        self.assertEqual(session["history"][-1]["reaction"], "needs")
        self.assertEqual(session["product_question_state"]["records"][0]["status"], "answer_detected")
        self.assertNotIn("t03-client", session["product_question_state"]["records"][0]["asked_turn_ids"])

    def test_terms_in_long_introduction_are_not_forgotten(self):
        session = step(new_session(self.bundle), "Здравствуйте, я представитель ВТБ. Выпуск и обслуживание бесплатны, подключаемые уведомления стоят 129 рублей в месяц.", "introduce", "explain_service_scope")
        self.assertEqual(session["history"][-1]["reaction"], "objection")
        self.assertIn("service_scope", session["product_question_state"]["covered"])
        self.assertFalse(session["product_question_state"]["records"])
        session = step(session, "Можно коротко объяснить, зачем вам дополнительная карта?", "offer_brief_explanation")
        self.assertEqual(session["history"][-1]["product_question_id"], "benefit_relevance")
        self.assertNotEqual(session["history"][-1].get("product_question_id"), "service_scope")

    def test_long_introduction_with_full_cashback_does_not_create_false_cost_question(self):
        session = step(new_session(self.bundle), "Представитель ВТБ. Выпуск и обслуживание бесплатны; уведомления платные при подключении. Категории кешбэка выбираете каждый месяц, предел — три тысячи рублей в месяц.", "introduce", "explain_service_scope", "explain_cashback_mechanics", "explain_cashback_limits")
        self.assertFalse(session["flags"]["pending_service_question"])
        self.assertIn("cashback_mechanics", session["product_question_state"]["covered"])
        self.assertIn("cashback_limit", session["product_question_state"]["covered"])

    def test_honest_unknown_is_not_an_automatic_meeting_agreement(self):
        session = step(self.deferred_cost(), "Тогда сохраняем встречу?", "propose_next_step")
        self.assertEqual(session["end_reason"], "client_unresolved_questions")
        self.assertEqual(session["client_outcome"], "declined")
        self.assertNotIn("service_scope", session["product_question_state"]["covered"])

    def test_combined_meeting_details_do_not_bypass_unanswered_topic(self):
        session = step(self.deferred_cost(), "Сохраняем встречу в 18:00, улица Примерная, 10? Паспорт будет при вас?", "propose_next_step", "confirm_time", "confirm_address", "ask_passport_availability")
        self.assertEqual(session["end_reason"], "client_unresolved_questions")
        self.assertEqual(session["history"][-1]["reaction"], "unanswered_goodbye")
        self.assertEqual(session["client_outcome"], "declined")

    def test_answer_after_deferral_allows_meeting_again(self):
        session = step(self.deferred_cost(), "Уточняю: выпуск и обслуживание бесплатны, уведомления при подключении — 129 рублей в месяц по базовому условию.", "explain_service_scope")
        session = step(session, "Сохраняем встречу?", "propose_next_step")
        self.assertEqual(session["status"], "active")
        self.assertEqual(session["history"][-1]["reaction"], "ask_time")

    def test_unanswered_goodbye_does_not_keep_a_prepared_address_followup(self):
        session = step(self.deferred_cost(), "Сохраняем встречу сегодня в 18:00? Паспорт у вас будет?", "propose_next_step", "confirm_time", "ask_passport_availability")
        self.assertEqual(session["history"][-1]["reaction"], "unanswered_goodbye")
        self.assertNotIn("continuation_reaction", session["history"][-1])
        self.assertNotIn("?", session["history"][-1]["text"])
        self.assertFalse(session["flags"]["meeting_ready"])

    def test_plain_unknown_does_not_become_a_promise_to_call_back(self):
        session = step(self.open_cost(), "Не знаю.", "defer_product_detail")
        context = client_context(session)
        self.assertTrue(context["employee_could_not_answer"])
        self.assertIn("не приписывай", context["required_meaning"].lower())

    def test_vague_limits_and_benefit_codes_do_not_close_questions(self):
        for text, code in [("Лимиты есть.", "explain_cashback_limits"), ("Много кешбэка.", "connect_benefit")]:
            analysis = bind_action_evidence({"actions": [{"code": code}]}, text)
            self.assertNotIn(code, [a["code"] for a in analysis["actions"]])
        for text in ["Три категории каждый месяц.", "Встреча в 18:00, дом 10."]:
            analysis = bind_action_evidence({"actions": [{"code": "explain_cashback_limits"}]}, text)
            self.assertNotIn("explain_cashback_limits", [a["code"] for a in analysis["actions"]])
        valid = bind_action_evidence({"actions": [{"code": "explain_cashback_limits"}]}, "Предел — три тысячи рублей в месяц.")
        self.assertEqual(valid["actions"][0]["code"], "explain_cashback_limits")

    def test_legacy_sessions_keep_controller_behavior(self):
        bundle = deepcopy(self.bundle)
        bundle["scenario"]["dialogue_features"].pop("responsive_dialogue")
        session = step(new_session(bundle), "Здравствуйте", "introduce")
        session = step(session, "Карта бесплатная", "explain_service")
        session = step(session, "А почему не хотите карту?", "ask_reason")
        self.assertEqual(session["history"][-1]["reaction"], "product_question")
        self.assertNotIn("conversation_memory", client_context(session))

    def test_natural_benefit_question_does_not_require_one_fixed_word(self):
        session = step(self.session, "Выпуск и обслуживание бесплатны, подключаемые уведомления платные.", "explain_service_scope")
        context = client_context(session)
        for text in ["Какие преимущества именно для меня?", "А стоит ли мне заводить вторую карту?", "Что я получу от второй карты?",
                     "Хорошо, а в чём конкретно выгода получить вашу карту, если я уже доволен своим банком?"]:
            self.assertEqual(validate_client_reply({"reaction": "product_question", "text": text}, context), text)

    def test_short_amount_can_answer_an_explicit_monthly_limit_question(self):
        for text in ["Три тысячи рублей.", "Три тысячи.", "3000"]:
            analysis = bind_action_evidence({"actions": [{"code": "explain_cashback_limits"}]}, text, current_question_id="cashback_limit")
            self.assertEqual(analysis["actions"][0]["code"], "explain_cashback_limits")

    def test_an_amount_without_period_or_context_is_not_a_full_monthly_limit(self):
        analysis = bind_action_evidence({"actions": [{"code": "explain_cashback_limits"}]}, "Три тысячи рублей.")
        self.assertEqual(analysis["actions"][0]["code"], "explain_cashback")


if __name__ == "__main__":
    unittest.main()
