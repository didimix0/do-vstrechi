from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from do_vstrechi.data import ROOT, validate_bundle
from legacy_fixtures import load_bundle
from do_vstrechi.knowledge import select_product_context


class ProductKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.bundle = load_bundle()
        self.product = self.bundle["product"]

    def test_user_materials_have_traceable_sources_and_exact_text_excerpts(self):
        raw_path = ROOT / "data/sources/vtb/2026-10-16-batch-01/received-text.txt"
        raw = raw_path.read_text(encoding="utf-8")
        for fact in self.product["facts"]:
            with self.subTest(fact=fact["id"]):
                if "U01" in fact["source_ids"]:
                    self.assertIn(fact["source_quote"], raw)
                else:
                    self.assertTrue(fact["evidence"])
                    self.assertEqual(fact["source_quote"], fact["evidence"][0]["quote"])
                self.assertTrue(fact["source_ids"])
        manifest = json.loads(raw_path.with_name("manifest.json").read_text(encoding="utf-8"))
        for item in manifest["files"]:
            data = (ROOT / item["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])

    def test_unidentified_month_and_segment_are_not_promoted_to_universal_terms(self):
        offers = self.product["conditional_offers"]
        snapshot = offers["cashback_snapshot"]
        self.assertIsNone(snapshot["offer_month"])
        self.assertFalse(snapshot["client_eligibility_known"])
        self.assertTrue(snapshot["selection_required"])
        self.assertIsNone(offers["grow_with_vtb"]["actions"]["credit"]["required_amount_rub"])
        self.assertEqual(offers["grow_with_vtb"]["levels"]["gold"]["notifications_monthly_rub"], 0)
        self.assertIsNone(offers["family_and_close"]["balance_rate_period"])

    def test_commission_free_cash_and_total_withdrawal_limits_remain_distinct(self):
        facts = {fact["id"]: fact for fact in self.product["facts"]}
        self.assertEqual(facts["P14"]["values"]["commission_free_monthly_rub"], 1_000_000)
        self.assertEqual(facts["P16"]["values"]["monthly_withdrawal_limit_rub"], 2_000_000)
        self.assertEqual(facts["P15"]["values"]["other_banks_commission_free_monthly_rub"], 200_000)
        self.assertIn("payroll", facts["P15"]["values"]["eligible_segments"])

    def test_context_uses_frozen_relevant_offers_and_retains_previous_topic(self):
        original = deepcopy(self.product)
        result = select_product_context(self.product, "А после бесплатного периода сколько стоит?", [
            {"role": "employee", "text": "Можно подключить подписку ВТБ Плюс."}])
        self.assertIn("VTB_Plus", result["conditional_offers"])
        self.assertNotIn("grow_with_vtb", result["conditional_offers"])
        self.assertNotIn("family_and_close", result["conditional_offers"])
        result["conditional_offers"]["VTB_Plus"]["standard"]["after_trial_monthly_rub"] = 0
        self.assertEqual(self.product, original)
        basic = select_product_context(self.product, "Выпуск и обслуживание бесплатные.", [])
        self.assertNotIn("conditional_offers", basic)
        self.assertEqual(basic["facts"], self.product["facts"])

    def test_missing_reference_in_extra_offer_is_rejected(self):
        self.product["conditional_offers"]["cashback_snapshot"]["source_ids"] = ["NON_EXISTENT"]
        with self.assertRaisesRegex(ValueError, "Предложение"):
            validate_bundle(self.bundle)

    def test_workbook_excerpts_resolve_to_archived_cells_and_unchanged_files(self):
        folder = ROOT / "data/sources/vtb/2026-10-16-batch-02"
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        books = json.loads((folder / "extracted-cells.json").read_text(encoding="utf-8"))
        for item in manifest["unique_workbooks"]:
            self.assertEqual(hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest(), item["sha256"])
        sources = {s["id"]: s for s in self.product["sources"]}
        source_cells = {(b["filename"], s["name"], c["cell"]): str(c["value"])
                        for b in books for s in b["sheets"] for c in s["cells"]}
        references = []
        def walk(value):
            if isinstance(value, dict):
                if {"source_id", "sheet", "cell", "quote"} <= value.keys():
                    references.append(value)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(self.product)
        self.assertGreater(len(references), 30)
        for ref in references:
            with self.subTest(ref=ref):
                filename = Path(sources[ref["source_id"]]["local_path"]).name
                self.assertIn(ref["quote"], source_cells[filename, ref["sheet"], ref["cell"]])
        for source in sources.values():
            for date in source.get("effective_dates", []):
                self.assertIn("01.10.2026", source_cells[Path(source["local_path"]).name, date["sheet"], date["cell"]])

    def test_card_and_cash_fees_keep_scope_and_calculation_base(self):
        facts = {f["id"]: f for f in self.product["facts"]}
        self.assertEqual(facts["P06"]["values"]["included_total_cards"], 5)
        self.assertTrue(facts["P06"]["values"]["count_includes_main_card"])
        self.assertEqual(facts["P06"]["values"]["beyond_included_issue_rub"], 649)
        self.assertEqual(facts["P14"]["values"]["excess_commission_percent"], 1.9)
        self.assertEqual(facts["P14"]["values"]["commission_base"], "amount_above_commission_free_monthly_limit")
        self.assertEqual(facts["P14"]["values"]["atm_scope"], ["VTB_group"])
        self.assertTrue(facts["P16"]["values"]["aggregate_across_cards"])
        self.assertEqual(facts["P17"]["values"]["minimum_rub"], 300)
        self.assertEqual(facts["P18"]["values"]["daily_QR_withdrawal_limit_rub"], 100000)
        self.assertTrue(facts["P18"]["values"]["included_in_total_limits"])
        self.assertTrue(all(f["tariff_effective_from"] == "2026-10-01" for f in facts.values() if f.get("evidence")))

    def test_family_conflict_and_subscription_promo_are_not_silently_resolved(self):
        family = self.product["conditional_offers"]["family_and_close"]
        self.assertIsNone(family["family_account_balance_rate_percent"])
        conflict = family["family_account_rate_conflict"]
        self.assertEqual(conflict["status"], "unresolved_do_not_grade")
        self.assertEqual(conflict["site_snapshot"]["percent"], 7)
        self.assertEqual(conflict["tariff"]["percent"], 13)
        self.assertEqual(conflict["tariff"]["period"], "annual")
        self.assertEqual(conflict["tariff"]["interest_bearing_balance_max_rub"], 100000)
        plus = self.product["conditional_offers"]["VTB_Plus"]
        self.assertIsNone(plus["standard"]["trial_days"])
        self.assertTrue(plus["standard"]["trial_for_first_connection_only"])
        self.assertEqual(plus["standard"]["trial_days_min"], 7)
        self.assertEqual(plus["standard"]["trial_days_max"], 90)
        self.assertTrue(plus["tariff_prices"]["special_offer_required_for_299"])
        self.assertEqual(plus["standard"]["after_trial_monthly_rub"], 349)

    def test_account_inactivity_fee_is_conditional_and_topic_selected(self):
        context = select_product_context(self.product, "Если я не буду пользоваться счётом, будет плата?", [])
        fee = context["conditional_offers"]["banking_tariffs"]["inactive_account_fee"]
        self.assertEqual(fee["inactive_calendar_days"], 180)
        self.assertTrue(fee["both_debits_and_credits_absent"])
        self.assertTrue(fee["fee_capped_by_remaining_balance"])
        self.assertEqual(fee["rub_balance_exempt_equal_zero_or_strictly_above"], [0, 10000])
        self.assertIn("payroll_client", fee["other_exemptions"])

    def test_missing_cell_source_reference_is_rejected(self):
        self.product["facts"][-1]["evidence"][0]["source_id"] = "NON_EXISTENT"
        with self.assertRaisesRegex(ValueError, "Выдержка"):
            validate_bundle(self.bundle)


if __name__ == "__main__":
    unittest.main()
