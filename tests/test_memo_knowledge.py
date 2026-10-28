from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from do_vstrechi.data import ROOT
from legacy_fixtures import load_bundle
from do_vstrechi.engine import new_session
from do_vstrechi.evaluation import evaluation_context, fact_sources
from do_vstrechi.knowledge import select_product_context


class MemoKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.bundle = load_bundle()
        self.product = self.bundle["product"]
        self.offers = self.product["conditional_offers"]

    def test_original_memo_and_extracted_quotes_are_traceable(self):
        folder = ROOT / "data/sources/vtb/2026-10-22-batch-03"
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        for item in manifest["files"]:
            self.assertEqual(hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest(), item["sha256"])
        paragraphs = {p["id"]: p["text"] for p in json.loads((folder / "extracted.json").read_text(encoding="utf-8"))["paragraphs"]}
        refs = []
        def walk(value):
            if isinstance(value, dict):
                if value.get("source_id") == "U04" and "paragraph" in value:
                    refs.append(value)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(self.offers)
        self.assertGreater(len(refs), 10)
        for ref in refs:
            self.assertEqual(ref["quote"], paragraphs[ref["paragraph"]])
        self.assertEqual(manifest["memo_declared_as_of"], "2026-10-17")
        self.assertEqual(manifest["received_on"], "2026-10-22")

    def test_gold_fee_checked_without_assigning_gold_to_customer(self):
        p03 = next(f for f in self.product["facts"] if f["id"] == "P03")
        self.assertEqual(p03["values"]["gold_monthly_rub"], 0)
        self.assertTrue(p03["values"]["level_must_be_established"])
        self.assertIn("W01", p03["source_ids"])
        self.assertIn("неизвестен", p03["text"])

    def test_welcome_two_percent_is_conditional_and_separate_from_historical_category(self):
        context = select_product_context(self.product, "Новому клиенту в первый месяц два процента кешбэка?", [])
        welcome = context["conditional_offers"]["new_client_offer"]["welcome_cashback"]
        self.assertEqual(welcome["percent"], 2)
        self.assertFalse(welcome["client_eligibility_known"])
        self.assertFalse(welcome["all_operations_without_exceptions"])
        self.assertFalse(welcome["applies_to_every_cardholder_permanently"])
        self.assertIsNone(welcome["exact_new_client_definition"])
        self.assertIsNone(self.offers["cashback_snapshot"]["offer_month"])

    def test_annual_limit_and_negative_recalculation_are_not_invented(self):
        rules = self.offers["cashback_rules"]
        self.assertEqual(rules["core"]["bank_payout_by_day_of_next_month"], 20)
        self.assertIsNone(rules["annual_limit"]["value_rub"])
        self.assertEqual(rules["annual_limit"]["reported_in_memo_rub"], 36000)
        self.assertFalse(rules["returns"]["negative_cashback_balance_confirmed"])
        self.assertFalse(rules["partner_promotions"]["addition_above_general_limit_guaranteed"])

    def test_pension_terms_are_scoped_and_do_not_override_ordinary_cash_rules(self):
        context = select_product_context(self.product, "Если я получаю пенсию в ВТБ, какие условия?", [])
        pension = context["conditional_offers"]["pensioner_offer"]
        self.assertTrue(pension["do_not_apply_to_ordinary_client"])
        self.assertFalse(pension["client_eligibility_known"])
        self.assertIsNone(pension["pharmacies"]["universal_percent"])
        self.assertNotIn("pensioner_offer", select_product_context(self.product, "Условия обслуживания карты?", []).get("conditional_offers", {}))
        facts = {f["id"]: f for f in self.product["facts"]}
        self.assertEqual(facts["P11"]["values"]["commission_free_monthly_rub"], 300000)
        self.assertEqual(facts["P15"]["values"]["other_banks_commission_free_monthly_rub"], 200000)

    def test_payout_context_and_history_topic_are_retained_without_other_offers(self):
        context = select_product_context(self.product, "А когда выплата?", [{"role": "employee", "text": "Речь о кешбэке."}])
        self.assertIn("cashback_rules", context["conditional_offers"])
        self.assertNotIn("pensioner_offer", context["conditional_offers"])
        self.assertNotIn("family_and_close", context["conditional_offers"])

    def test_different_holding_periods_do_not_merge(self):
        self.assertEqual(self.offers["grow_with_vtb"]["actions"]["hold"]["minimum_days"], 25)
        self.assertTrue(self.offers["grow_with_vtb"]["actions"]["hold"]["days_may_be_nonconsecutive"])
        self.assertEqual(self.offers["extra_cashback_mechanics"]["balances"]["holding_days_reported"], 30)
        self.assertTrue(self.offers["extra_cashback_mechanics"]["weekend"]["requires_VTB_Plus"])

    def test_review_can_resolve_sources_for_new_scoped_offer(self):
        session = new_session(self.bundle)
        session.update(status="ended", end_reason="employee_ended")
        session["history"].append({"id": "e1", "role": "employee", "text": "Для новых клиентов в первый месяц описаны 2% кешбэка; ваше право нужно проверить."})
        context = evaluation_context(session)
        offer = next(f for f in context["product"]["facts"] if f["id"] == "O:new_client_offer")
        ref = fact_sources(session, {"fact_id": offer["id"], "quote": offer["text"]})
        self.assertTrue(any(s["id"] == "W02" and s["urls"] for s in ref["sources"]))

    def test_frozen_sessions_keep_previous_source_copy(self):
        session = new_session(self.bundle)
        original = deepcopy(session["materials"])
        self.product["conditional_offers"]["new_client_offer"]["welcome_cashback"]["percent"] = 999
        self.assertEqual(session["materials"], original)


if __name__ == "__main__":
    unittest.main()
