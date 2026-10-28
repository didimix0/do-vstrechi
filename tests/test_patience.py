from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from do_vstrechi.client import client_context, validate_client_reply
from do_vstrechi.contracts import ContractError, bind_action_evidence
from do_vstrechi.data import validate_bundle
from legacy_fixtures import load_bundle
from do_vstrechi.engine import advance, new_session, review_packet


def step(session, *codes, text="Не могу ответить по существу"):
    return advance(session, text, {"actions": [{"code": code, "quote": text} for code in codes]}, mode="test")


class QuestionPatienceTests(unittest.TestCase):
    def setUp(self):
        self.bundle = load_bundle()
        self.session = step(new_session(self.bundle), "introduce", text="Я представитель ВТБ, звоню перед встречей для выдачи карты.")

    def source_question(self):
        return step(self.session, "possible_product_error", text="Кешбэк без ограничений.")

    def test_vague_website_answer_and_refusal_do_not_create_endless_source_requests(self):
        session = self.source_question()
        session = step(session, "unclear", text="На сайте.")
        self.assertEqual(session["history"][-1]["reaction"], "source_question")
        session = step(session, "unclear", text="Нет, не могу сказать, где.")
        self.assertEqual(session["status"], "ended")
        self.assertEqual(session["history"][-1]["reaction"], "unanswered_goodbye")
        self.assertEqual(session["client_outcome"], "declined")
        self.assertEqual(sum(t.get("reaction") == "source_question" for t in session["history"]), 2)
        self.assertEqual(session["training_verdict"], "not_evaluated")

    def test_honest_unknown_after_source_question_can_end_without_demanding_another_link(self):
        session = step(self.source_question(), "defer_product_detail", text="Не знаю.")
        self.assertEqual(session["history"][-1]["reaction"], "unanswered_goodbye")
        self.assertEqual(session["status"], "ended")

    def test_second_unsuccessful_answer_switches_topic_and_keeps_question_unresolved(self):
        session = step(self.session, "explain_service", text="Карта бесплатная.")
        session = step(session, "unclear")
        session = step(session, "unclear")
        self.assertEqual(session["history"][-1]["product_question_id"], "benefit_relevance")
        self.assertEqual(session["history"][-1]["switched_from_question_id"], "service_scope")
        record = session["product_question_state"]["records"][0]
        self.assertEqual(record["status"], "unresolved")
        self.assertEqual(len(record["asked_turn_ids"]), 2)
        self.assertNotIn("service_scope", session["product_question_state"]["covered"])
        self.assertTrue(client_context(session)["previous_question_unresolved"])
        packet = review_packet(session)
        self.assertEqual(packet["explanation_evidence"]["questions"][0]["status"], "unresolved")

    def test_two_unresolved_topics_end_the_call_without_fabricated_question_records(self):
        session = step(self.session, "explain_service", text="Карта бесплатная.")
        for _ in range(4):
            session = step(session, "unclear")
        self.assertEqual(session["status"], "ended")
        self.assertEqual(session["end_reason"], "client_unresolved_questions")
        turns = {t["id"]: t for t in session["history"]}
        for record in session["product_question_state"]["records"]:
            self.assertEqual(len(record["asked_turn_ids"]), 2)
            self.assertTrue(all(turns[tid].get("product_question_id") == record["question_id"] for tid in record["asked_turn_ids"]))

    def test_unresolved_topic_does_not_turn_into_meeting_agreement(self):
        session = step(self.session, "explain_service", text="Карта бесплатная.")
        for _ in range(2):
            session = step(session, "unclear")
        session = step(session, "propose_next_step", text="Давайте всё же встретимся.")
        self.assertEqual(session["status"], "ended")
        self.assertEqual(session["client_outcome"], "declined")
        self.assertEqual(len(session["product_question_state"]["records"][1]["asked_turn_ids"]), 1)

    def test_employee_can_supply_missing_detail_after_topic_switch(self):
        session = step(self.session, "explain_service", text="Карта бесплатная.")
        for _ in range(2):
            session = step(session, "unclear")
        session = step(session, "explain_service_scope", text="Выпуск и обслуживание карты бесплатны, уведомления оплачиваются отдельно при подключении.")
        session = step(session, "connect_benefit", text="Можно оставить привычную карту, а эту рассмотреть как дополнительную без платы за обслуживание.")
        session = step(session, "propose_next_step", text="Сохраняем встречу?")
        self.assertEqual(session["history"][-1]["reaction"], "ask_time")
        self.assertEqual(session["product_question_state"]["records"][0]["status"], "answer_detected")

    def test_an_explicit_answer_prevents_an_unnecessary_departure(self):
        session = self.source_question()
        session = step(session, "unclear", text="На сайте.")
        session = step(session, "clarify_product_terms", "explain_service_scope", text="Уточню сказанное: выпуск и обслуживание карты бесплатны, уведомления — отдельная платная услуга при подключении.")
        self.assertEqual(session["status"], "active")
        self.assertFalse(session["flags"]["pending_product_clarification"])

    def test_incomprehensible_conversation_has_a_finite_limit(self):
        session = new_session(self.bundle)
        for _ in range(3):
            session = step(session, "unclear", text="Набор непонятных слов")
        self.assertEqual(session["status"], "ended")
        self.assertEqual(session["history"][-1]["reaction"], "unanswered_goodbye")

    def test_old_frozen_materials_keep_their_original_behavior(self):
        legacy = deepcopy(self.bundle)
        legacy["scenario"]["dialogue_features"].pop("question_patience")
        session = step(new_session(legacy), "introduce")
        session = step(session, "possible_product_error")
        for _ in range(4):
            session = step(session, "unclear")
        self.assertEqual(session["status"], "active")
        self.assertNotIn("question_patience_events", session)

    def test_generated_goodbye_must_not_restart_the_question(self):
        session = self.source_question()
        session = step(step(session, "unclear"), "unclear")
        context = client_context(session)
        self.assertEqual(validate_client_reply({"reaction": "unanswered_goodbye", "text": "Тогда пока откажусь. До свидания."}, context), "Тогда пока откажусь. До свидания.")
        for text in ["До свидания. Где всё-таки условия?", "До свидания. Пришлите мне ссылку.", "Понятно."]:
            with self.assertRaises(ContractError):
                validate_client_reply({"reaction": "unanswered_goodbye", "text": text}, context)

    def test_question_budget_must_be_a_small_positive_integer(self):
        for value in [0, -1, 10, True]:
            bundle = deepcopy(self.bundle)
            bundle["scenario"]["question_policy"]["max_asks_per_topic"] = value
            with self.assertRaises(ValueError):
                validate_bundle(bundle)

    def test_bare_website_pointer_cannot_become_an_explanation(self):
        for text in ["На сайте.", "Посмотрите на официальном сайте ВТБ.", "В приложении ВТБ Онлайн"]:
            analysis = bind_action_evidence({"actions": [{"code": "explain_service"}, {"code": "clarify_product_terms"}]}, text)
            self.assertEqual(analysis["actions"], [{"code": "unclear", "quote": text}])

    def test_website_reference_does_not_remove_actual_terms(self):
        text = "На сайте указано: выпуск и обслуживание карты бесплатны, уведомления оплачиваются отдельно при подключении."
        analysis = bind_action_evidence({"actions": [{"code": "explain_service_scope"}]}, text)
        self.assertEqual(analysis["actions"][0]["code"], "explain_service_scope")


if __name__ == "__main__":
    unittest.main()
