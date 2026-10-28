from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from do_vstrechi.contracts import ContractError
from legacy_fixtures import load_bundle
from do_vstrechi.engine import new_session, review_packet
from do_vstrechi.evaluation import apply_evaluation, evaluation_context, finalize_evaluation, validate_evaluation
from do_vstrechi.gigachat import GigaChatAnalyzer, ProviderError, unique_json_object
from do_vstrechi.webapp import ApiError, TrainerApp


def completed_session():
    session = new_session(load_bundle())
    session.update(status="ended", phase="ended", end_reason="appointment_confirmed", client_outcome="appointment_agreed")
    session["history"] = [
        {"id": "e1", "role": "employee", "text": "Здравствуйте, я Дмитрий, представитель ВТБ. Звоню о встрече для получения заказанной карты."},
        {"id": "c1", "role": "client", "text": "Я передумал, пользуюсь картой другого банка."},
        {"id": "e2", "role": "employee", "text": "Привычную карту можно оставить. Выпуск и обслуживание самой карты бесплатны, а уведомления при подключении оплачиваются отдельно: 129 рублей в месяц по базовому условию; для отдельных уровней действуют другие условия. Эту карту можно рассмотреть как дополнительную без платы за обслуживание. Сохраняем встречу сегодня в 18:00: город Учебный, улица Примерная, дом 10? Оригинал паспорта РФ будет при вас?"},
        {"id": "c2", "role": "client", "text": "Да, всё верно, паспорт есть. До встречи!"}]
    session["observations"] = [{"code": "explain_service", "quote": session["history"][2]["text"], "turn_id": "e2"}]
    return session


def valid_payload(session):
    employee = [t for t in session["history"] if t["role"] == "employee"]
    facts = session["materials"]["product"]["facts"]
    fact = next(f for f in facts if f["id"] == "P01")
    payload = {"criteria": [{"id": c["id"], "status": "not_applicable" if c["id"] == "C07" else "met",
                           "reason": "Окончательного отказа не было." if c["id"] == "C07" else "Подтверждается репликой сотрудника.",
                           "next_step": "", "evidence": [] if c["id"] == "C07" else [{"turn_id": employee[0 if c["id"] == "C01" else -1]["id"], "quote": employee[0 if c["id"] == "C01" else -1]["text"]}]}
                          for c in session["materials"]["rubric"]["criteria"]],
            "claims": [{"evidence": {"turn_id": employee[-1]["id"], "quote": employee[-1]["text"]},
                        "status": "supported", "explanation": "Стоимость соответствует сохранённым материалам.",
                        "facts": [{"fact_id": "P01", "quote": fact["text"][:100]}], "correction": []}],
            "coverage_complete": True, "coverage_note": ""}
    if any(c["id"] == "C10" for c in session["materials"]["rubric"]["criteria"]):
        payload["speech_review"] = {"level": "appropriate", "summary": "Речь сотрудника деловая и уважительная.", "findings": []}
    return payload


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.session = completed_session()
        self.payload = valid_payload(self.session)

    def test_complete_assessment_passes_without_arbitrary_score(self):
        result = finalize_evaluation(self.payload, self.session)
        self.assertEqual(result["training_verdict"], "passed")
        self.assertNotIn("score", result)

    def test_fixed_wire_keys_and_empty_optional_fields_do_not_invent_evidence(self):
        wire = deepcopy(self.payload)
        wire["criteria"] = {c["id"]: {k: v for k, v in c.items() if k != "id"} for c in wire["criteria"]}
        wire["criteria"]["C07"].pop("evidence")
        wire["criteria"]["C07"].pop("next_step")
        wire["claims"][0].pop("correction")
        original = deepcopy(wire)
        self.assertEqual(finalize_evaluation(wire, self.session)["training_verdict"], "passed")
        self.assertEqual(wire, original)
        wire["criteria"]["C01"].pop("evidence")
        checked = finalize_evaluation(wire, self.session)
        self.assertEqual(next(c for c in checked["criteria"] if c["id"] == "C01")["status"], "requires_review")
        self.assertEqual(checked["training_verdict"], "requires_review")

    def test_duplicate_json_keys_are_rejected_before_they_can_overwrite_a_verdict(self):
        with self.assertRaises(ValueError):
            json.loads('{"criteria":{"C01":{"status":"not_met"},"C01":{"status":"met"}}}', object_pairs_hook=unique_json_object)

    def test_evaluation_transport_uses_compact_schema_and_restores_timeout(self):
        provider = GigaChatAnalyzer({"GIGACHAT_AUTH_KEY": "synthetic-key-for-test"})
        response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(self.payload)}}]}
        token = {"access_token": "synthetic-token", "expires_at": int((time.time() + 1800) * 1000)}
        timeouts = []
        def transport(url, body, headers):
            timeouts.append(provider.request_timeout)
            return token if url == provider.TOKEN_URL else response
        with patch.object(provider, "_post", side_effect=transport) as post:
            updated = apply_evaluation(self.session, provider)
            request = json.loads(post.call_args.args[1])
        self.assertEqual(updated["training_verdict"], "passed")
        self.assertEqual(request["response_format"]["type"], "json_schema")
        self.assertNotIn('"maxLength"', json.dumps(request["response_format"]["schema"]))
        self.assertIn("output_schema", json.loads(request["messages"][-1]["content"]))
        self.assertEqual(provider.request_timeout, 30)
        self.assertEqual(timeouts, [90, 90])
        with patch.object(provider, "_structured", side_effect=ProviderError("Синтетический таймаут")):
            with self.assertRaises(ProviderError):
                provider.evaluate_session(self.session)
        self.assertEqual(provider.request_timeout, 30)

    def test_doubt_cannot_make_objection_criteria_not_applicable(self):
        self.session["history"][1]["reaction"] = "objection"
        for c in self.payload["criteria"]:
            if c["id"] in {"C02", "C03"}:
                c["status"] = "not_applicable"
        self.assertEqual(finalize_evaluation(self.payload, self.session)["training_verdict"], "requires_review")

    def test_a_missing_required_question_requires_another_attempt(self):
        next(c for c in self.payload["criteria"] if c["id"] == "C09").update(status="not_met", evidence=[], reason="Вопрос о паспорте не задан.")
        self.assertEqual(finalize_evaluation(self.payload, self.session)["training_verdict"], "retry")

    def test_fabricated_quote_is_rejected(self):
        self.payload["criteria"][0]["evidence"] = [{"turn_id": "e1", "quote": "Этих слов нет"}]
        with self.assertRaises(ContractError):
            validate_evaluation(self.payload, self.session)

    def test_client_words_alone_cannot_prove_employee_success_or_discard_other_criteria(self):
        self.payload["criteria"][0]["evidence"] = [{"turn_id": "c1"}]
        checked = finalize_evaluation(self.payload, self.session)
        self.assertEqual(checked["criteria"][0]["status"], "requires_review")
        self.assertEqual(checked["rating"]["met"], 8)
        self.assertEqual(len(checked["criteria"]), 10)

    def test_missing_or_duplicate_criterion_and_extra_verdict_are_rejected(self):
        variants = [deepcopy(self.payload) for _ in range(3)]
        variants[0]["criteria"].pop()
        variants[1]["criteria"][-1]["id"] = "C01"
        variants[2]["training_verdict"] = "passed"
        for payload in variants:
            with self.assertRaises(ContractError):
                validate_evaluation(payload, self.session)

    def test_product_source_ids_and_quotes_must_exist(self):
        for ref in [{"fact_id": "P999", "quote": "Несуществующее условие"}, {"fact_id": "P01", "quote": "Сотруднику положен кешбэк 100 процентов"}]:
            payload = deepcopy(self.payload)
            payload["claims"][0]["facts"] = [ref]
            with self.assertRaises(ContractError):
                validate_evaluation(payload, self.session)

    def test_wire_fact_ids_are_bound_to_actual_source_text_by_server(self):
        self.payload["claims"][0].pop("facts")
        self.payload["claims"][0]["fact_ids"] = ["P01", "P03"]
        result = validate_evaluation(self.payload, self.session)
        known = {f["id"]: f["text"] for f in evaluation_context(self.session)["product"]["facts"]}
        self.assertEqual(result["claims"][0]["facts"][0]["quote"], known["P01"])
        self.payload["claims"][0]["fact_ids"] = ["P999"]
        with self.assertRaises(ContractError):
            validate_evaluation(self.payload, self.session)

    def test_wire_turn_ids_are_bound_without_model_copying_source_words(self):
        for criterion in self.payload["criteria"]:
            criterion["evidence"] = [{"turn_id": e["turn_id"]} for e in criterion["evidence"]]
        self.payload["claims"][0]["evidence"] = {"turn_id": "e2"}
        result = validate_evaluation(self.payload, self.session)
        self.assertEqual(result["claims"][0]["evidence"]["quote"], self.session["history"][2]["text"])
        self.payload["claims"][0]["evidence"] = {"turn_id": "invented"}
        with self.assertRaises(ContractError):
            validate_evaluation(self.payload, self.session)

    def test_original_suspicious_statement_cannot_disappear_after_a_correction(self):
        self.session["observations"].append({"code": "possible_product_error", "turn_id": "e1"})
        self.assertEqual(finalize_evaluation(self.payload, self.session)["training_verdict"], "requires_review")

    def test_unsupported_verdict_is_downgraded_without_discarding_the_whole_review(self):
        self.payload["claims"][0]["facts"] = []
        result = finalize_evaluation(self.payload, self.session)
        self.assertEqual(result["claims"][0]["status"], "not_established")
        self.assertFalse(result["claims"][0]["facts"])
        self.assertEqual(len(result["criteria"]), 10)
        self.assertEqual(result["training_verdict"], "requires_review")

    def test_unknown_value_is_reviewed_instead_of_being_invented(self):
        self.payload["claims"][0].update(status="not_established", facts=[])
        result = finalize_evaluation(self.payload, self.session)
        self.assertEqual(result["training_verdict"], "requires_review")

    def test_unfounded_comparison_does_not_crash_or_become_a_verified_false_fact(self):
        self.payload["claims"][0].update(status="contradicted", facts=[], explanation="Наша карта лучше.")
        result = finalize_evaluation(self.payload, self.session)
        self.assertEqual(result["claims"][0]["status"], "not_established")
        self.assertEqual(result["training_verdict"], "requires_review")

    def test_payroll_withdrawal_allowance_is_not_an_unconditional_ordinary_card_term(self):
        self.session["history"][2]["text"] = "До двухсот тысяч можно снять бесплатно."
        self.payload = valid_payload(self.session)
        next(c for c in self.payload["criteria"] if c["id"] == "C09").update(status="not_met", evidence=[])
        self.payload["claims"][0].pop("facts")
        self.payload["claims"][0]["fact_ids"] = ["P15"]
        result = finalize_evaluation(self.payload, self.session)
        self.assertEqual(result["claims"][0]["status"], "not_established")

    def test_error_cannot_coexist_with_unqualified_positive_result(self):
        self.payload["claims"][0]["status"] = "contradicted"
        self.assertNotEqual(finalize_evaluation(self.payload, self.session)["training_verdict"], "passed")

    def test_confirmed_unfixed_error_with_failed_criteria_requires_retraining(self):
        self.payload["claims"][0]["status"] = "contradicted"
        for c in self.payload["criteria"]:
            if c["id"] in {"C04", "C05"}:
                c.update(status="not_met", reason="Неверное обещание осталось неисправленным.")
        self.assertEqual(finalize_evaluation(self.payload, self.session)["training_verdict"], "retry")
        result = finalize_evaluation(self.payload, self.session)
        self.assertEqual(next(c for c in result["criteria"] if c["id"] == "C02")["status"], "not_met")
        self.assertEqual(next(c for c in result["criteria"] if c["id"] == "C03")["status"], "not_met")

    def test_correction_requires_actual_later_words(self):
        claim = self.payload["claims"][0]
        claim.update(status="corrected", correction=[{"turn_id": "e1", "quote": self.session["history"][0]["text"]}])
        with self.assertRaises(ContractError):
            validate_evaluation(self.payload, self.session)
        self.session["history"].append({"id": "e3", "role": "employee", "text": "Уточню: выпуск и обслуживание самой карты бесплатны."})
        claim["correction"] = [{"turn_id": "e3", "quote": self.session["history"][-1]["text"]}]
        self.assertEqual(finalize_evaluation(self.payload, self.session)["training_verdict"], "passed")

    def test_claimed_correction_without_a_reference_is_uncertain_not_an_automatic_pass(self):
        self.payload["claims"][0].update(status="corrected", correction=[])
        result = finalize_evaluation(self.payload, self.session)
        self.assertEqual(result["training_verdict"], "requires_review")
        self.assertEqual(result["claims"][0]["status"], "not_established")
        self.assertEqual(result["claims"][0]["evidence"], self.payload["claims"][0]["evidence"])

    def test_inconsistent_correction_status_is_reviewed_with_both_actual_replicas(self):
        self.session["history"].append({"id": "e3", "role": "employee", "text": "Уточню: выпуск и обслуживание самой карты бесплатны."})
        self.payload["claims"][0].update(status="contradicted", correction=[{"turn_id": "e3"}])
        result = finalize_evaluation(self.payload, self.session)
        self.assertEqual(result["training_verdict"], "requires_review")
        self.assertEqual(result["claims"][0]["correction"][0]["quote"], self.session["history"][-1]["text"])

    def test_incomplete_conversation_never_gets_automatic_pass_or_failure(self):
        for ending in ["client_hangup", "turn_limit", "employee_stopped"]:
            session = deepcopy(self.session)
            session["end_reason"] = ending
            self.assertEqual(finalize_evaluation(self.payload, session)["training_verdict"], "requires_review")

    def test_no_claims_or_incomplete_coverage_cannot_get_a_pass(self):
        for change in [{"claims": []}, {"coverage_complete": False}]:
            payload = deepcopy(self.payload)
            payload.update(change)
            self.assertEqual(finalize_evaluation(payload, self.session)["training_verdict"], "requires_review")

    def test_required_steps_cannot_be_hidden_as_not_applicable(self):
        for code in ["C01", "C06", "C08", "C09"]:
            payload = deepcopy(self.payload)
            next(c for c in payload["criteria"] if c["id"] == code)["status"] = "not_applicable"
            self.assertEqual(finalize_evaluation(payload, self.session)["training_verdict"], "requires_review")

    def test_passport_reminder_does_not_count_as_question_even_if_model_says_met(self):
        self.session["history"][2]["text"] = "Подготовьте оригинал паспорта РФ к встрече."
        self.payload = valid_payload(self.session)
        result = finalize_evaluation(self.payload, self.session)
        self.assertEqual(next(c for c in result["criteria"] if c["id"] == "C09")["status"], "requires_review")

    def test_evaluator_receives_text_not_intermediate_action_codes_and_ignores_unused_offers(self):
        self.session["observations"].append({"code": "SECRET_ACTION_MARKER"})
        context = evaluation_context(self.session)
        self.assertNotIn("SECRET_ACTION_MARKER", json.dumps(context))
        self.assertFalse(any(f["id"] == "O:family_and_close" for f in context["product"]["facts"]))

    def test_completed_evaluation_has_sources_and_does_not_change_conversation(self):
        fake = Evaluator()
        before = deepcopy(self.session)
        updated = apply_evaluation(self.session, fake)
        self.assertEqual(self.session, before)
        self.assertEqual(updated["history"], before["history"])
        packet = review_packet(updated)
        self.assertEqual(packet["evaluation_status"], "completed")
        self.assertTrue(packet["claims"][0]["facts"][0]["sources"])
        updated["history"][0]["text"] += " Изменение истории."
        self.assertEqual(review_packet(updated)["training_verdict"], "not_evaluated")


class Evaluator:
    def __init__(self):
        self.count = 0
        self.fail = False
        self.metrics = {"model": "synthetic", "usage": {}, "elapsed_seconds": 0}

    def evaluate_session(self, session):
        self.count += 1
        if self.fail:
            raise ProviderError("Синтетическая ошибка")
        return valid_payload(session)


class EvaluationServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.fake = Evaluator()
        self.app = TrainerApp(Path(self.folder.name) / "eval.sqlite3", lambda: self.fake)
        session = completed_session()
        session.update(mode="gigachat", requests={})
        self.app.store.create(session)
        self.session = self.app.public(session)

    def tearDown(self):
        self.folder.cleanup()

    def body(self):
        return {"request_id": uuid.uuid4().hex, "revision": self.session["revision"]}

    def test_concurrent_and_new_requests_reuse_successful_evaluation(self):
        body = self.body()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.app.evaluate(self.session["id"], body), range(2)))
        self.assertEqual(self.fake.count, 1)
        self.assertEqual(results[0], results[1])
        self.session = results[0]["session"]
        self.app.evaluate(self.session["id"], self.body())
        self.assertEqual(self.fake.count, 1)

    def test_failure_preserves_dialogue_and_same_request_does_not_spend_again(self):
        self.fake.fail = True
        body = self.body()
        original = self.app.get(self.session["id"])["history"]
        for _ in range(2):
            with self.assertRaises(ApiError) as caught:
                self.app.evaluate(self.session["id"], body)
            self.assertEqual(caught.exception.status, 502)
            self.session = caught.exception.state
        self.assertEqual(self.fake.count, 1)
        self.assertEqual(self.app.get(self.session["id"])["history"], original)
        self.assertEqual(self.app.review(self.session["id"])["training_verdict"], "not_evaluated")
        self.fake.fail = False
        self.assertEqual(self.app.evaluate(self.session["id"], self.body())["review"]["training_verdict"], "passed")

    def test_reading_review_does_not_call_api_and_active_session_cannot_be_evaluated(self):
        self.app.review(self.session["id"])
        self.assertEqual(self.fake.count, 0)
        active = self.app.create({"mode": "guided", "ending": "appointment"})
        with self.assertRaises(ApiError) as caught:
            self.app.evaluate(active["id"], {"request_id": uuid.uuid4().hex, "revision": 0})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.fake.count, 0)


if __name__ == "__main__":
    unittest.main()
