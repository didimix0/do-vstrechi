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
from do_vstrechi.client import client_context, validate_client_reply
from do_vstrechi.contracts import ContractError, bind_action_evidence
from do_vstrechi.conversation import naturalize_client_reply
from legacy_fixtures import load_bundle
from do_vstrechi.engine import advance, new_session
from do_vstrechi.gigachat import GigaChatAnalyzer, ProviderError
from do_vstrechi.webapp import TrainerApp


def step(session, codes, text="Учебная реплика сотрудника"):
    return advance(session, text, {"actions": [{"code": code, "quote": text} for code in codes]}, mode="gigachat")


class FakeClientProvider:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0
        self.client_calls = 0
        self.metrics = {"usage": {"prompt_tokens": 12}, "elapsed_seconds": 0}

    def analyze(self, session, text):
        self.calls += 1
        return {"actions": [{"code": "introduce", "quote": text}]}

    def generate_client_reply(self, session):
        self.client_calls += 1
        if self.fail:
            raise ProviderError("Тестовая ошибка генерации")
        return "Здравствуйте. Да, встречу назначали, но я уже передумал. Своей картой пользуюсь давно, ещё одна мне зачем?"


class ClientContractTests(unittest.TestCase):
    def setUp(self):
        bundle = load_bundle()
        bundle['scenario']['dialogue_features'].pop('product_questions', None)
        self.session = new_session(bundle)
        self.session["client_reply_mode"] = "natural"
        self.session = step(self.session, ["introduce"])

    def test_context_has_history_but_no_future_responses_or_rubric(self):
        context = client_context(self.session)
        self.assertEqual(context["reaction"], "objection")
        self.assertEqual(context["history"][-1]["role"], "employee")
        self.assertNotIn("replies", context)
        self.assertNotIn("rubric", context)
        self.assertNotIn("ending_preference", context)
        self.assertNotIn("product", context)
        self.assertNotIn(self.session["history"][-1], context["history"])

    def test_generator_changes_wording_without_changing_state_or_evidence(self):
        original = deepcopy(self.session)
        result = naturalize_client_reply(self.session, FakeClientProvider())
        self.assertEqual(self.session, original)
        self.assertNotEqual(result["history"][-1]["text"], original["history"][-1]["text"])
        for key in ("flags", "phase", "status", "client_outcome", "training_verdict", "observations", "turn_count"):
            self.assertEqual(result[key], original[key])
        self.assertEqual(result["history"][-1]["reply_source"], "generated")
        self.assertEqual([call["role"] for call in result["calls"]], ["action_analysis", "client_generation"])

    def test_failed_generation_is_visible_and_preserves_scripted_reply(self):
        result = naturalize_client_reply(self.session, FakeClientProvider(fail=True))
        self.assertEqual(result["history"][-1]["text"], self.session["history"][-1]["text"])
        self.assertEqual(result["history"][-1]["reply_source"], "scripted_fallback")
        self.assertTrue(result["client_reply_notice"])
        self.assertEqual(result["calls"][-1]["status"], "error")

    def test_role_change_fabricated_rate_and_premature_agreement_are_rejected(self):
        context = client_context(self.session)
        bad = [
            {"reaction": "appointment", "text": "Тогда буду ждать."},
            {"reaction": "objection", "text": "Сотрудник: ваш кешбэк отличный."},
            {"reaction": "objection", "text": "У меня кешбэк 99 процентов."},
            {"reaction": "objection", "text": "Зачёт, переходите на следующий уровень."},
            {"reaction": "objection", "text": "Приезжайте, я жду вас."},
            {"reaction": "objection", "text": "Карта не нужна.", "passed": True},
        ]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(ContractError):
                validate_client_reply(payload, context)

    def test_combined_reason_and_needs_is_one_reaction(self):
        session = step(self.session, ["ask_reason", "ask_needs"])
        self.assertEqual(client_context(session)["reaction"], "reason_and_needs")
        self.assertIn("удобно", session["history"][-1]["text"])

    def test_appointment_and_refusal_rules_cannot_be_replaced_by_generator(self):
        session = step(self.session, ["explain_service"])
        session = step(session, ["propose_next_step"])
        context = client_context(session)
        self.assertEqual(context["reaction"], "ask_time")
        with self.assertRaises(ContractError):
            validate_client_reply({"reaction": "ask_time", "text": "Всё верно, до свидания."}, context)
        session = step(self.session, ["pressure"])
        context = client_context(session)
        with self.assertRaises(ContractError):
            validate_client_reply({"reaction": "firm_refusal", "text": "Нет, давайте всё-таки обсудим."}, context)

    def test_hangup_has_no_generator_and_clears_old_fallback_notice(self):
        self.session["ending_preference"] = "hangup"
        session = step(self.session, ["explain_service"])
        session["client_reply_notice"] = "Предыдущее уведомление"
        session = step(session, ["propose_next_step"])
        provider = FakeClientProvider()
        result = naturalize_client_reply(session, provider)
        self.assertEqual(result["history"][-1]["type"], "client_hangup")
        self.assertEqual(provider.client_calls, 0)
        self.assertEqual(result["client_reply_notice"], "")

    def test_old_sessions_remain_scripted(self):
        del self.session["client_reply_mode"]
        provider = FakeClientProvider()
        result = naturalize_client_reply(self.session, provider)
        self.assertEqual(result, self.session)
        self.assertEqual(provider.client_calls, 0)

    def test_evidence_is_exact_source_text_and_extra_model_quotes_are_rejected(self):
        text = "Можно я коротко объясню условия?"
        result = bind_action_evidence({"actions": [{"code": "offer_brief_explanation"}]}, text)
        self.assertEqual(result["actions"][0]["quote"], text)
        with self.assertRaises(ContractError):
            bind_action_evidence({"actions": [{"code": "offer_brief_explanation", "quote": "объясну"}]}, text)

    def test_client_cannot_invent_having_read_product_terms(self):
        session = step(self.session, ["possible_product_error"])
        with self.assertRaises(ContractError):
            validate_client_reply({"reaction": "source_question", "text": "Нигде не видел такого предложения. Где условия?"}, client_context(session))
        self.assertEqual(validate_client_reply({"reaction": "source_question", "text": "А где можно посмотреть эти условия?"}, client_context(session)),
                         "А где можно посмотреть эти условия?")


class ClientTransportTests(unittest.TestCase):
    def test_real_adapter_uses_separate_client_prompt_and_strict_reaction(self):
        provider = GigaChatAnalyzer({"GIGACHAT_AUTH_KEY": "synthetic-key-for-test"})
        session = step(new_session(load_bundle()), ["introduce"])
        answer = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "reaction": "objection", "text": "Здравствуйте. Да, помню про встречу, но я передумал, мне своей карты хватает."})}}],
            "usage": {"prompt_tokens": 80, "completion_tokens": 20}}
        token = {"access_token": "synthetic-token", "expires_at": int((time.time() + 1800) * 1000)}
        with patch.object(provider, "_post", side_effect=[token, answer]) as post:
            text = provider.generate_client_reply(session)
        self.assertIn("передумал", text)
        payload = json.loads(post.call_args.args[1])
        self.assertEqual(payload["messages"][0]["content"], provider.client_prompt)
        self.assertEqual(payload["response_format"]["schema"]["properties"]["reaction"]["enum"], ["objection"])
        self.assertEqual(provider.metrics["role"], "client_generation")
        self.assertEqual(provider.metrics["status"], "ok")
        self.assertEqual(provider.metrics["usage"]["completion_tokens"], 20)

    def test_duplicate_failed_generation_does_not_repeat_either_model_call(self):
        with tempfile.TemporaryDirectory() as folder:
            provider = FakeClientProvider(fail=True)
            app = TrainerApp(Path(folder) / "session.sqlite3", lambda: provider)
            session = app.create({"mode": "gigachat", "ending": "appointment"})
            body = {"request_id": uuid.uuid4().hex, "revision": 0, "text": "Здравствуйте, представитель ВТБ, звоню о встрече."}
            first = app.mutate(session["id"], body)
            second = app.mutate(session["id"], body)
            self.assertEqual(first, second)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(provider.client_calls, 1)
            self.assertTrue(second["client_reply_notice"])
            self.assertEqual(len(app.get(session["id"])["calls"]), 2)


if __name__ == "__main__":
    unittest.main()
