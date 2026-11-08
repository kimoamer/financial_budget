import frappe
from erpnext.accounts.report.cash_flow import cash_flow as cf
from frappe.tests.utils import FrappeTestCase

from financial_budget.tests import fixtures as fx
from financial_budget.tests import planning_data as pdata
from financial_budget.tests.fixtures import acc

KEY = "jun_2027"


def sections(result):
	return {r.get("section"): r for r in result[1] if r and r.get("section")}


def q(label):
	return "'" + label + "'"


def adjustment(amount, section="Financing", account=None, **kw):
	return frappe.get_doc(
		doctype="Cash Flow Planning Adjustment",
		company=fx.COMPANY,
		scenario="Base",
		posting_date="2027-01-15",
		section=section,
		amount=amount,
		account=account,
		description="Loan drawdown",
		**kw,
	)


class TestCashFlowBudget(fx.BudgetTestCase, FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		pdata.setup_actuals_and_budgets()

	def setUp(self):
		super().setUp()
		fx.plan(pdata.plan_rows())

	def run_yearly(self, **kw):
		return fx.run(cf, fx.filters(periodicity="Yearly", **kw))

	def test_planned_lines_and_unreconciled_difference(self):
		result, original = self.run_yearly()
		fx.assert_actuals_unchanged(self, result, original)
		s = sections(result)
		_ = frappe._
		self.assertAlmostEqual(s[q(_("Profit for the year"))]["fb_budget_" + KEY], 8400)
		self.assertAlmostEqual(s[_("Depreciation")]["fb_budget_" + KEY], 1200)
		self.assertAlmostEqual(s[_("Net Change in Accounts Receivable")]["fb_budget_" + KEY], -2000)
		self.assertAlmostEqual(s[_("Net Change in Accounts Payable")]["fb_budget_" + KEY], 500)
		self.assertAlmostEqual(s[_("Net Change in Inventory")]["fb_budget_" + KEY], 0)  # no stock, no plan
		self.assertAlmostEqual(s[q(_("Net Cash from Operations"))]["fb_budget_" + KEY], 8100)
		self.assertAlmostEqual(s[q(_("Net Cash from Investing"))]["fb_budget_" + KEY], -3000)
		self.assertAlmostEqual(s[q(_("Net Cash from Financing"))]["fb_budget_" + KEY], 0)
		self.assertAlmostEqual(s[q(_("Net Change in Cash"))]["fb_budget_" + KEY], 5100)

		self.assertAlmostEqual(s[_("Planned Opening Cash (Balance Sheet Plan)")]["fb_budget_" + KEY], 1000)
		self.assertAlmostEqual(s[_("Planned Cash per Balance Sheet Plan")]["fb_budget_" + KEY], 10100)
		diff = s[_("Unreconciled Difference")]
		self.assertAlmostEqual(diff["fb_budget_" + KEY], -4000)  # the loan is not a standard line
		self.assertEqual(diff["fb_status"], "Unreconciled")

	def test_adjustment_reconciles(self):
		adjustment(4000, account=acc("FB Loan")).submit()
		result, original = self.run_yearly()
		fx.assert_actuals_unchanged(self, result, original)
		s = sections(result)
		self.assertAlmostEqual(s[frappe._("Planned Adjustments")]["fb_budget_" + KEY], 4000)
		self.assertAlmostEqual(s[q(frappe._("Net Cash from Financing"))]["fb_budget_" + KEY], 4000)
		closing = s[frappe._("Planned Closing Cash (Opening + Net Change)")]["fb_budget_" + KEY]
		self.assertAlmostEqual(closing, s[frappe._("Planned Cash per Balance Sheet Plan")]["fb_budget_" + KEY])
		self.assertAlmostEqual(closing, 1000 + 9100)
		self.assertEqual(s[frappe._("Unreconciled Difference")]["fb_status"], "Reconciled")

	def test_adjustment_cannot_double_count(self):
		self.assertRaises(frappe.ValidationError, adjustment(100, account=acc("FB Debtors")).insert)
		self.assertRaises(frappe.ValidationError, adjustment(100, account=acc("FB Cash")).insert)
		self.assertRaises(frappe.ValidationError, adjustment(0).insert)

	def test_incomplete_plan(self):
		# monthly, not accumulated: an annual plan has no month-start balances
		result, original = fx.run(cf, fx.filters(periodicity="Monthly"))
		fx.assert_actuals_unchanged(self, result, original)
		s = sections(result)
		footer = s[q(frappe._("Net Cash from Operations"))]
		self.assertIsNone(footer["fb_budget_jul_2026"])
		self.assertIn("Plan Incomplete", footer["fb_status"])
		self.assertIn("Plan Incomplete", result[2])

	def test_accumulated_monthly_uses_fiscal_year_start(self):
		result, _o = fx.run(cf, fx.filters(periodicity="Monthly", accumulated_values=1))
		s = sections(result)
		self.assertAlmostEqual(s[q(frappe._("Net Change in Cash"))]["fb_budget_" + KEY], 5100)
		self.assertIsNone(s[q(frappe._("Net Change in Cash"))]["fb_budget_jul_2026"])

	def test_cost_center_filter_is_refused(self):
		result, original = self.run_yearly(cost_center=[fx.main_cc()])
		self.assertEqual(result[1], original[1])
		self.assertIn("company-level", result[2])

	def test_opening_closing_rows_kept(self):
		# ERPNext's opening balance needs the company on a fiscal year (rolled back with the test)
		fy = frappe.get_doc("Fiscal Year", fx.FY)
		fy.append("companies", {"company": fx.COMPANY})
		fy.save()
		result, original = self.run_yearly(show_opening_and_closing_balance=1)
		fx.assert_actuals_unchanged(self, result, original)
