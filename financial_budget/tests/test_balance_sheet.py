import frappe
from erpnext.accounts.report.balance_sheet import balance_sheet as bs
from frappe.tests.utils import FrappeTestCase

from financial_budget.tests import fixtures as fx
from financial_budget.tests import planning_data as pdata
from financial_budget.tests.fixtures import acc


def yearly(**kw):
	return fx.filters(periodicity="Yearly", accumulated_values=1, **kw)


class TestBudgetedBalanceSheet(fx.BudgetTestCase, FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		pdata.setup_actuals_and_budgets()

	def test_plan_balances_and_integrates(self):
		doc = fx.plan(pdata.plan_rows())
		self.assertEqual(doc.is_balanced, 1)
		self.assertAlmostEqual(doc.periods[0].planned_profit_loss, 8400)
		self.assertAlmostEqual(doc.periods[0].total_assets, 13900)

		result, original = fx.run(bs, yearly())
		fx.assert_actuals_unchanged(self, result, original)
		rows = fx.rows_by_account(result)
		self.assertAlmostEqual(rows[acc("FB Cash")]["fb_budget_jun_2027"], 10100)
		self.assertAlmostEqual(rows[acc("FB Cash")]["fb_variance_jun_2027"], 1750 - 10100)
		self.assertAlmostEqual(rows[acc("FB Current Assets")]["fb_budget_jun_2027"], 12100)
		self.assertAlmostEqual(rows[acc("FB Fixed Assets")]["fb_budget_jun_2027"], 1800)
		self.assertAlmostEqual(rows["'" + frappe._("Total {0} ({1})").format(frappe._("Asset"), frappe._("Debit")) + "'"]["fb_budget_jun_2027"], 13900)
		provisional = rows["'" + frappe._("Provisional Profit / Loss (Credit)") + "'"]
		self.assertAlmostEqual(provisional["fb_budget_jun_2027"], 8400)
		credit = rows["'" + frappe._("Total (Credit)") + "'"]
		self.assertAlmostEqual(credit["fb_budget_jun_2027"], 4500 + 1000 + 8400)  # = planned assets

	def test_unbalanced_plan_cannot_be_submitted(self):
		doc = fx.plan(pdata.plan_rows(cash_closing=10000), submit=False)
		self.assertEqual(doc.is_balanced, 0)
		self.assertAlmostEqual(doc.periods[0].difference, -100)
		self.assertRaises(frappe.ValidationError, doc.submit)

	def test_validations(self):
		for bad in (
			[(pdata.END, acc("FB Current Assets"), 0, 1)],  # group
			[(pdata.END, acc("FB Sales"), 0, 1)],  # income
			[("2027-03-31", acc("FB Cash"), 0, 1)],  # not the annual period end
			[(pdata.END, acc("FB Cash"), 0, 1), (pdata.END, acc("FB Cash"), 0, 1)],  # twice
		):
			self.assertRaises(frappe.ValidationError, fx.plan, bad, submit=False)

	def test_one_submitted_plan_per_scenario(self):
		fx.plan(pdata.plan_rows())
		self.assertRaises(frappe.ValidationError, fx.plan, pdata.plan_rows())
		fx.plan(pdata.plan_rows(), scenario="Stretch")  # another scenario is fine

	def test_manual_profit_loss(self):
		doc = fx.plan(pdata.plan_rows(cash_closing=6700), profit_loss_source="Manual", periods={pdata.END: 5000})
		self.assertAlmostEqual(doc.periods[0].planned_profit_loss, 5000)
		self.assertEqual(doc.docstatus, 1)

	def test_missing_balance_is_marked_incomplete(self):
		# balanced without the loan (cash 4000 lower) -> the loan has an actual balance but no plan
		fx.plan(pdata.plan_rows(cash_closing=6100, with_loan=False))
		result, _o = fx.run(bs, yearly())
		rows = fx.rows_by_account(result)
		self.assertIsNone(rows[acc("FB Loan")]["fb_budget_jun_2027"])
		self.assertEqual(rows[acc("FB Loan")]["fb_status"], "Not Planned")
		self.assertEqual(rows[acc("FB Liabilities")]["fb_status"], "Plan Incomplete")

	def test_unplanned_periods_are_missing_not_zero(self):
		fx.plan(pdata.plan_rows())
		result, original = fx.run(bs, fx.filters(periodicity="Quarterly", accumulated_values=1))
		fx.assert_actuals_unchanged(self, result, original)
		cash = fx.rows_by_account(result)[acc("FB Cash")]
		self.assertIsNone(cash["fb_budget_sep_2026"])
		self.assertAlmostEqual(cash["fb_budget_jun_2027"], 10100)
		self.assertIn("Plan Incomplete", result[2])

	def test_no_plan_and_scenario_filter(self):
		fx.plan(pdata.plan_rows())
		result, original = fx.run(bs, yearly(budget_scenario="Does Not Exist"))
		self.assertEqual(result[1], original[1])
		self.assertIn("Plan Incomplete", result[2])

	def test_non_accumulated_and_cost_center_are_refused(self):
		fx.plan(pdata.plan_rows())
		for f in (fx.filters(periodicity="Yearly", accumulated_values=0), yearly(cost_center=[fx.main_cc()])):
			result, original = fx.run(bs, f)
			self.assertEqual(result[0], original[0])
			self.assertEqual(result[1], original[1])

	def test_monthly_plan(self):
		rows = [("2026-07-31", a, o, m) for _e, a, o, m in pdata.plan_rows()]
		doc = fx.plan(rows, frequency="Monthly", profit_loss_source="Manual", periods={"2026-07-31": 8400})
		self.assertEqual(doc.docstatus, 1)
		result, _o = fx.run(bs, fx.filters(periodicity="Monthly", accumulated_values=1))
		cash = fx.rows_by_account(result)[acc("FB Cash")]
		self.assertAlmostEqual(cash["fb_budget_jul_2026"], 10100)
		self.assertIsNone(cash["fb_budget_aug_2026"])
