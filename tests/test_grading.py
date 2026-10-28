from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from do_vstrechi.evaluation import finalize_evaluation, session_rating
from do_vstrechi.engine import review_packet
from do_vstrechi.grading import benefit_detail_issue, cashback_detail_issue, quantitative_detail_issue, completion_rating
from test_evaluation import completed_session, valid_payload


class ConcreteBenefitTests(unittest.TestCase):
    def test_cashback_terms_can_continue_in_next_sentences_without_borrowing_other_products(self):
        text=('Кешбэк начисляется рублями за подходящие покупки в выбранных категориях. '
              'Категории выбираются каждый месяц в ВТБ Онлайн. В предложении указано до 15 процентов в отдельных категориях, а не на любые покупки. '
              'Общий предел — 3000 рублей в месяц, это максимум, а не гарантированная сумма.')
        self.assertIsNone(cashback_detail_issue(text))
        self.assertIsNotNone(cashback_detail_issue('Кешбэк до 15 процентов в категориях. Переводы через СБП без комиссии до 300 тысяч рублей в месяц.'))
        self.assertIsNotNone(cashback_detail_issue('Много кешбэка в категориях. Вклад под 15 процентов. Уведомления 129 рублей в месяц.'))

    def test_slogan_is_not_an_explanation_even_if_evaluator_awards_met(self):
        session = completed_session()
        session["history"][2]["text"] = "Много кэшбэка."
        result = finalize_evaluation(valid_payload(session), session)
        by_id = {c["id"]: c for c in result["criteria"]}
        for code in ("C02", "C03", "C04"):
            self.assertEqual(by_id[code]["status"], "not_met")
            self.assertEqual(by_id[code]["model_status"], "met")
            self.assertIn("конкрет", by_id[code]["reason"])
        self.assertLess(result["rating"]["value"], 100)

    def test_incidental_numbers_and_category_count_do_not_replace_cashback_value(self):
        for text in ["Много кешбэка, встреча в 18:00, дом 10.", "Есть кешбэк, три категории каждый месяц.",
                     "Есть кешбэк. Уведомления стоят 129 рублей в месяц."]:
            self.assertIsNotNone(cashback_detail_issue(text))

    def test_rate_without_eligible_purchases_or_monthly_limit_is_incomplete(self):
        for text in ["Кешбэк до 15%.", "Кешбэк до 15% за покупки в выбранных категориях."]:
            self.assertIsNotNone(cashback_detail_issue(text))

    def test_spoken_numbers_work_and_price_zero_does_not_require_a_literal_digit(self):
        text = "Кешбэк до пятнадцати процентов в выбранных категориях. Общий предел кешбэка — три тысячи рублей в месяц."
        self.assertIsNone(cashback_detail_issue(text))
        self.assertIsNone(benefit_detail_issue("Выпуск и обслуживание бесплатны, уведомления — отдельная платная услуга при подключении."))
        self.assertIsNotNone(benefit_detail_issue("Карта бесплатная."))

    def test_real_explanation_in_another_turn_requires_correct_evidence_not_false_failure(self):
        session = completed_session()
        session["history"].insert(2, {"id": "vague", "role": "employee", "text": "Много кешбэка."})
        payload = valid_payload(session)
        for criterion in payload["criteria"]:
            if criterion["id"] in {"C02", "C03"}:
                criterion["evidence"] = [{"turn_id": "vague"}]
        result = finalize_evaluation(payload, session)
        self.assertEqual(next(c for c in result["criteria"] if c["id"] == "C03")["status"], "requires_review")

    def test_complete_cost_benefit_does_not_hide_missing_cashback_details(self):
        text = "Выпуск и обслуживание бесплатны. А ещё много кешбэка."
        self.assertIsNone(benefit_detail_issue(text))
        self.assertIsNotNone(cashback_detail_issue(text))

    def test_having_numbers_does_not_cancel_a_contradiction(self):
        session = completed_session()
        payload = valid_payload(session)
        payload["claims"][0]["status"] = "contradicted"
        result = finalize_evaluation(payload, session)
        self.assertNotEqual(result["training_verdict"], "passed")
        self.assertTrue(result["rating"]["provisional"])

    def test_paid_notification_needs_price_and_period(self):
        self.assertIsNotNone(quantitative_detail_issue("Уведомления оплачиваются отдельно."))
        self.assertIsNone(quantitative_detail_issue("Уведомления при подключении оплачиваются отдельно: 129 рублей в месяц по базовому тарифу."))
        self.assertIsNone(quantitative_detail_issue("Выпуск и обслуживание карты бесплатны."))


class CompletionRatingTests(unittest.TestCase):
    def rating(self, *statuses):
        return completion_rating([{"status": s} for s in statuses])

    def test_equal_weight_score_excludes_inapplicable(self):
        score = self.rating("met", "met", "not_met", "not_applicable")
        self.assertEqual(score["value"], 67)
        self.assertEqual(score["applicable"], 3)
        self.assertFalse(score["provisional"])

    def test_pending_items_give_no_credit_and_make_rating_provisional(self):
        score = self.rating("met", "requires_review", "not_applicable")
        self.assertEqual(score["value"], 50)
        self.assertTrue(score["provisional"])

    def test_empty_denominator_does_not_produce_zero_or_one_hundred(self):
        for statuses in [(), ("not_applicable",)]:
            self.assertIsNone(self.rating(*statuses)["value"])

    def test_uncertain_overall_result_keeps_rating_preliminary(self):
        session = completed_session()
        session["end_reason"] = "client_hangup"
        evaluation = finalize_evaluation(valid_payload(session), session)
        self.assertTrue(evaluation["rating"]["provisional"])
        session["evaluation"] = evaluation
        self.assertTrue(session_rating(session)["provisional"])

    def test_review_and_history_share_score_and_unassessed_session_has_none(self):
        session = completed_session()
        self.assertIsNone(session_rating(session))
        session["evaluation"] = finalize_evaluation(valid_payload(session), session)
        self.assertEqual(review_packet(session)["rating"], session_rating(session))
        self.assertEqual(session_rating(session)["value"], 100)
        changed = deepcopy(session)
        changed["history"][0]["text"] += " Изменение."
        self.assertIsNone(session_rating(changed))


if __name__ == "__main__":
    unittest.main()
