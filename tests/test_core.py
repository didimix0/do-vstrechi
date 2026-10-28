from copy import deepcopy
import json
from pathlib import Path
import ssl
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from do_vstrechi.cli import DEMO_TURNS
from do_vstrechi.contracts import ContractError, validate_analysis
from do_vstrechi.data import validate_bundle
from legacy_fixtures import load_bundle
from do_vstrechi.engine import advance, new_session, review_packet
from do_vstrechi.gigachat import GigaChatAnalyzer
from do_vstrechi.storage import SessionStore, StaleSessionError


def step(session, code, text="Проверочная реплика"):
    return advance(session, text, {"actions": [{"code": code, "quote": text}]}, mode="test")


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.bundle = load_bundle()
        # Базовые переходы отдельно от новых уточнений; полный сценарий проверяется в test_questions.
        self.bundle["scenario"]["dialogue_features"].pop("product_questions", None)
        self.session = new_session(self.bundle)

    def complete(self, ending="appointment"):
        session = new_session(self.bundle, ending)
        for code, text in DEMO_TURNS:
            session = step(session, code, text)
            if session["status"] == "ended" or session["phase"] == "final_refusal":
                break
        return session

    def test_appointment_agreement_does_not_pass_training(self):
        session = self.complete()
        self.assertEqual(session["client_outcome"], "appointment_agreed")
        self.assertEqual(session["end_reason"], "appointment_confirmed")
        packet = review_packet(session)
        self.assertEqual(packet["training_verdict"], "not_evaluated")
        self.assertTrue(all(c["status"] == "requires_review" for c in packet["criteria"]))

    def test_decline_after_full_conversation_is_not_automatic_failure(self):
        session = self.complete("decline")
        self.assertEqual(session["client_outcome"], "declined")
        self.assertEqual(session["phase"], "final_refusal")
        session = step(session, "respect_refusal", "Понял, спасибо. До свидания.")
        self.assertEqual(session["end_reason"], "client_declined")
        self.assertEqual(review_packet(session)["training_verdict"], "not_evaluated")

    def test_agreement_to_listen_is_not_appointment_agreement(self):
        session = step(self.session, "introduce")
        session = step(session, "offer_brief_explanation")
        self.assertEqual(session["history"][-1]["text"], "Да, расскажите.")
        self.assertEqual(session["client_outcome"], "undecided")
        self.assertEqual(session["status"], "active")

    def test_spending_survey_and_cashback_are_not_mandatory(self):
        session = step(self.session, "introduce")
        session = step(session, "explain_service")
        session = step(session, "propose_next_step")
        self.assertEqual(session["client_outcome"], "ready_to_meet")
        session = step(session, "confirm_time")
        session = step(session, "confirm_address")
        self.assertEqual(session["client_outcome"], "appointment_agreed")
        self.assertFalse(session["flags"]["needs_known"])
        self.assertFalse(session["flags"]["cashback_discussed"])

    def test_question_raised_by_client_must_be_addressed(self):
        session = step(self.session, "introduce")
        session = step(session, "explain_cashback")
        session = step(session, "propose_next_step")
        self.assertEqual(session["history"][-1]["text"], self.bundle["scenario"]["replies"]["missing_service"])
        self.assertEqual(session["client_outcome"], "undecided")
        session = step(session, "explain_service")
        session = step(session, "propose_next_step")
        self.assertEqual(session["client_outcome"], "ready_to_meet")
        session = step(session, "confirm_time")
        session = step(session, "confirm_address")
        self.assertEqual(session["client_outcome"], "appointment_agreed")

    def test_both_meeting_details_are_required(self):
        session = step(self.session, "introduce")
        session = step(session, "explain_service")
        session = step(session, "propose_next_step")
        self.assertEqual(session["phase"], "confirmation")
        self.assertEqual(session["history"][-1]["text"], self.bundle["scenario"]["replies"]["ask_time"])
        session = step(session, "confirm_time")
        self.assertEqual(session["status"], "active")
        self.assertEqual(session["history"][-1]["text"], self.bundle["scenario"]["replies"]["ask_address"])
        session = step(session, "confirm_address")
        self.assertEqual(session["status"], "active")
        session = step(session, "ask_passport_availability", "Оригинал паспорта РФ будет с собой?")
        self.assertEqual(session["status"], "ended")
        self.assertEqual(review_packet(session)["appointment_evidence"]["verification"], "requires_review")

    def test_meeting_details_can_be_combined_in_one_turn(self):
        session = step(self.session, "introduce")
        session = step(session, "explain_service")
        text = "Встречаемся сегодня в 18:00 по адресу: г. Учебный, ул. Примерная, д. 10? Оригинал паспорта РФ будет с собой?"
        session = advance(session, text, {"actions": [
            {"code": code, "quote": text} for code in ("propose_next_step", "confirm_time", "confirm_address", "ask_passport_availability")
        ]}, mode="test")
        self.assertEqual(session["status"], "ended")
        self.assertEqual(session["client_outcome"], "appointment_agreed")

    def test_product_question_cannot_be_skipped_after_possible_error(self):
        session = step(self.session, "introduce")
        session = step(session, "explain_service")
        session = step(session, "possible_product_error")
        session = step(session, "propose_next_step")
        self.assertEqual(session["client_outcome"], "undecided")
        self.assertEqual(session["history"][-1]["text"], self.bundle["scenario"]["replies"]["source_question"])
        session = step(session, "explain_service")
        self.assertTrue(session["flags"]["pending_product_clarification"])
        text = "Уточню сказанное: выпуск и обслуживание бесплатные, у дополнительных услуг отдельные тарифы."
        session = advance(session, text, {"actions": [
            {"code": "clarify_product_terms", "quote": text},
            {"code": "explain_service", "quote": text}
        ]}, mode="test")
        self.assertFalse(session["flags"]["pending_product_clarification"])
        self.assertEqual(review_packet(session)["training_verdict"], "not_evaluated")

    def test_rude_refusal_still_requires_respectful_closing(self):
        session = self.complete("rude_decline")
        self.assertEqual(session["history"][-1]["text"], self.bundle["scenario"]["replies"]["rude_decline"])
        session = step(session, "ask_needs")
        self.assertFalse(session["flags"]["needs_known"])
        self.assertEqual(session["phase"], "final_refusal")
        session = step(session, "end_meeting")
        self.assertEqual(session["status"], "ended")

    def test_hangup_is_an_event_and_prevents_further_replies(self):
        session = self.complete("hangup")
        self.assertEqual(session["history"][-1]["role"], "event")
        self.assertEqual(session["history"][-1]["type"], "client_hangup")
        self.assertEqual(session["end_reason"], "client_hangup")
        self.assertTrue(review_packet(session)["evidence_incomplete"])
        self.assertEqual(review_packet(session)["training_verdict"], "not_evaluated")
        with self.assertRaises(ValueError):
            step(session, "respect_refusal")

    def test_premature_next_step_keeps_objection(self):
        session = step(self.session, "introduce")
        session = step(session, "propose_next_step")
        self.assertEqual(session["status"], "active")
        self.assertEqual(session["client_outcome"], "undecided")
        self.assertEqual(session["history"][-1]["text"], self.bundle["scenario"]["replies"]["missing_explanation"])

    def test_possible_error_does_not_advance_terms_or_become_verified_error(self):
        session = step(self.session, "possible_product_error", "Кешбэк будет вообще за всё")
        self.assertFalse(session["flags"]["cashback_discussed"])
        self.assertEqual(session["observations"][0]["verification"], "requires_review")

    def test_pressure_causes_refusal_and_blocks_further_persuasion(self):
        session = step(self.session, "pressure")
        session = step(session, "explain_service")
        self.assertTrue(session["flags"]["firm_refusal"])
        self.assertFalse(session["flags"]["service_discussed"])
        self.assertEqual(session["client_outcome"], "declined")
        session = step(session, "respect_refusal")
        self.assertEqual(session["status"], "ended")

    def test_materials_are_frozen_for_the_session(self):
        self.bundle["product"]["facts"][0]["text"] = "Изменение после начала"
        self.assertNotEqual(self.session["materials"]["product"]["facts"][0]["text"], "Изменение после начала")

    def test_multiple_questions_disclose_both_circumstances(self):
        session = step(self.session, "introduce")
        text = "Почему отказались? Какие покупки у вас частые?"
        session = advance(session, text, {"actions": [
            {"code": "ask_reason", "quote": "Почему отказались?"},
            {"code": "ask_needs", "quote": "Какие покупки у вас частые?"}]}, mode="test")
        self.assertTrue(session["flags"]["reason_known"] and session["flags"]["needs_known"])
        self.assertIn(self.bundle["scenario"]["replies"]["reason"], session["history"][-1]["text"])
        self.assertIn(self.bundle["scenario"]["replies"]["needs"], session["history"][-1]["text"])

    def test_turn_limit_stops_without_pass(self):
        session = self.session
        for _ in range(self.bundle["scenario"]["max_turns"]):
            session = step(session, "unclear")
        self.assertEqual(session["end_reason"], "turn_limit")
        self.assertEqual(session["training_verdict"], "not_evaluated")
        with self.assertRaises(ValueError):
            step(session, "ask_reason")


class ValidationTests(unittest.TestCase):
    def test_fabricated_evidence_is_rejected(self):
        with self.assertRaises(ContractError):
            validate_analysis({"actions": [{"code": "ask_reason", "quote": "Несуществующая цитата"}]}, "Здравствуйте")

    def test_injected_pass_field_is_rejected(self):
        with self.assertRaises(ContractError):
            validate_analysis({"actions": [{"code": "ask_reason", "quote": "Почему?"}], "passed": True}, "Почему?")

    def test_unknown_actions_are_rejected(self):
        with self.assertRaises(ContractError):
            validate_analysis({"actions": [{"code": "unlock_level_3", "quote": "Зачёт"}]}, "Зачёт")

    def test_missing_product_reference_is_rejected(self):
        bundle = load_bundle()
        bundle["rubric"]["criteria"][4]["fact_ids"].append("P_NONEXISTENT")
        with self.assertRaises(ValueError):
            validate_bundle(bundle)

    def test_missing_source_reference_is_rejected(self):
        bundle = load_bundle()
        bundle["product"]["facts"][0]["source_ids"] = ["S_NONEXISTENT"]
        with self.assertRaises(ValueError):
            validate_bundle(bundle)

    def test_missing_appointment_details_are_rejected(self):
        bundle = load_bundle()
        del bundle["scenario"]["appointment"]["address"]
        with self.assertRaises(ValueError):
            validate_bundle(bundle)


class StorageTests(unittest.TestCase):
    def test_round_trip_and_stale_write_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(Path(folder) / "session.sqlite3")
            session = new_session(load_bundle())
            store.create(session)
            copy = store.get(session["id"])
            self.assertEqual(copy["materials"], session["materials"])
            updated = step(copy, "ask_reason")
            saved = store.save(updated, 0)
            self.assertEqual(saved["revision"], 1)
            with self.assertRaises(StaleSessionError):
                store.save(updated, 0)
            self.assertEqual(store.get(session["id"])["revision"], 1)


class FakeGigaChat(GigaChatAnalyzer):
    def __init__(self, answers):
        super().__init__({"GIGACHAT_AUTH_KEY": "test-key-never-a-real-secret"})
        self.answers = iter(answers)
        self.requests = []

    def _post(self, url, body, headers):
        self.requests.append((url, body, headers))
        return next(self.answers)


class ProviderTests(unittest.TestCase):
    def token(self):
        return {"access_token": "test-access-token", "expires_at": int((time.time() + 1800) * 1000)}

    def answer(self, quote=None):
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "actions": [{"code": "ask_reason"} | ({"quote": quote} if quote is not None else {})]})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}

    def test_schema_request_and_token_cache_with_fake_transport(self):
        provider = FakeGigaChat([self.token(), self.answer(), self.answer()])
        session = new_session(load_bundle())
        for _ in range(2):
            self.assertEqual(provider.analyze(session, "Почему?")["actions"][0], {"code": "ask_reason", "quote": "Почему?"})
        self.assertEqual(len(provider.requests), 3)
        request = json.loads(provider.requests[1][1])
        self.assertEqual(request["response_format"]["type"], "json_schema")
        self.assertTrue(request["response_format"]["strict"])
        context = json.loads(request["messages"][1]["content"])
        self.assertEqual(context["known_appointment"], session["materials"]["scenario"]["appointment"])
        self.assertEqual(provider.metrics["usage"]["prompt_tokens"], 100)
        self.assertEqual(provider.ssl_context.verify_mode, ssl.CERT_REQUIRED)

    def test_invalid_model_quote_cannot_enter_the_controller(self):
        provider = FakeGigaChat([self.token(), self.answer("Другая реплика")])
        session = new_session(load_bundle())
        with self.assertRaises(ContractError):
            provider.analyze(session, "Почему?")
        self.assertEqual(provider.metrics["status"], "error")
        self.assertEqual(session["turn_count"], 0)


if __name__ == "__main__":
    unittest.main()
