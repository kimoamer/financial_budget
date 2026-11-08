import frappe
from erpnext.accounts.report.profit_and_loss_statement import profit_and_loss_statement as pl
from frappe.tests.utils import FrappeTestCase

from financial_budget.tests import fixtures as fx
from financial_budget.tests.fixtures import acc


class TestProfitAndLossBudget(fx.BudgetTestCase, FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fx.setup_company()
		fx.set_settings(enabled=1, enable_profit_and_loss=1, display_zero_budget_accounts=0)
		cash = acc("FB Cash")
		fx.journal("2026-07-10", [(cash, 2000, 0), (acc("FB Sales"), 0, 2000)])
		fx.journal("2026-07-15", [(acc("FB Rent"), 250, 0), (cash, 0, 250)])
		fx.journal("2026-07-20", [(acc("FB Salaries"), 100, 0), (cash, 0, 100)])
		fx.journal("2026-08-10", [(cash, 1500, 0), (acc("FB Sales"), 0, 1500)])
		fx.journal("2026-08-15", [(acc("FB Rent"), 150, 0), (cash, 0, 150)])
		fx.journal("2025-09-01", [(cash, 700, 0), (acc("FB Sales"), 0, 700)])  # previous fiscal year
		dist = fx.distribution("FB Dist", {"July": 20, "August": 10})
		fx.budget([(acc("FB Sales"), 12000)], distribution=dist)
		fx.budget([(acc("FB Rent"), 2400), (acc("FB Services"), 1200)])
		fx.budget([(acc("FB Sales"), 6000)], fiscal_year=fx.prev_fy())

	def test_monthly_budget_variance_and_rollup(self):
		result, original = fx.run(pl, fx.filters())
		fx.assert_actuals_unchanged(self, result, original)
		rows = fx.rows_by_account(result)

		sales = rows[acc("FB Sales")]
		self.assertEqual(sales["jul_2026"], 2000)
		self.assertAlmostEqual(sales["fb_budget_jul_2026"], 2400)  # 20% of 12000
		self.assertAlmostEqual(sales["fb_variance_jul_2026"], -400)
		self.assertAlmostEqual(sales["fb_variance_pct_jul_2026"], -16.67, places=2)
		self.assertAlmostEqual(sales["fb_budget_aug_2026"], 1200)
		self.assertAlmostEqual(sales["fb_variance_aug_2026"], 300)
		self.assertAlmostEqual(sales["fb_budget_total"], 12000)

		rent = rows[acc("FB Rent")]
		self.assertAlmostEqual(rent["fb_budget_jul_2026"], 200)  # 2400 / 12, expense shown debit-positive
		# favourable = positive: income actual - budget, expense budget - actual (overspend is negative)
		self.assertAlmostEqual(rent["fb_variance_jul_2026"], -50)
		self.assertAlmostEqual(rent["fb_variance_pct_jul_2026"], -25)
		self.assertAlmostEqual(rows[acc("FB Operating Expenses")]["fb_variance_jul_2026"], 200 - 350)
		total_expense = rows["'" + frappe._("Total {0} ({1})").format(frappe._("Expense"), frappe._("Debit")) + "'"]
		self.assertAlmostEqual(total_expense["fb_variance_jul_2026"], 200 - 350)
		net_row = rows["'" + frappe._("Profit for the year") + "'"]
		self.assertAlmostEqual(net_row["fb_variance_jul_2026"], (2000 - 350) - (2500 - 200))

		# group rows roll up budgeted leaves including hidden FB Services (1200 / 12)
		self.assertAlmostEqual(rows[acc("FB Operating Income")]["fb_budget_jul_2026"], 2400 + 100)
		self.assertAlmostEqual(rows[acc("FB Operating Expenses")]["fb_budget_jul_2026"], 200)

		net = rows["'" + frappe._("Profit for the year") + "'"]
		self.assertAlmostEqual(net["fb_budget_jul_2026"], 2500 - 200)
		self.assertAlmostEqual(net["fb_budget_total"], 13200 - 2400)

	def test_budget_column_comes_before_actual(self):
		result, _o = fx.run(pl, fx.filters(periodicity="Quarterly"))
		cols = [(c["fieldname"], c["label"]) for c in result[0]]
		names = [f for f, _l in cols]
		i = names.index("fb_budget_sep_2026")
		self.assertEqual(names[i : i + 4], ["fb_budget_sep_2026", "sep_2026", "fb_variance_sep_2026", "fb_variance_pct_sep_2026"])
		j = names.index("fb_budget_total")
		self.assertEqual(names[j : j + 2], ["fb_budget_total", "total"])
		labels = dict(cols)
		self.assertTrue(labels["sep_2026"].endswith(frappe._("Actual")))
		self.assertEqual(labels["total"], frappe._("Total") + " " + frappe._("Actual"))
		self.assertNotIn(frappe._("Actual"), labels["account"])

	def test_missing_budget_is_not_zero(self):
		result, _o = fx.run(pl, fx.filters())
		rows = fx.rows_by_account(result)
		salaries = rows[acc("FB Salaries")]
		self.assertIsNone(salaries["fb_budget_jul_2026"])
		self.assertIsNone(salaries["fb_variance_jul_2026"])
		self.assertEqual(salaries["fb_status"], "No Budget")
		self.assertEqual(rows[acc("FB Operating Expenses")]["fb_status"], "Partial Budget")

	def test_accumulated_quarterly_yearly(self):
		acc_result, _o = fx.run(pl, fx.filters(accumulated_values=1))
		sales = fx.rows_by_account(acc_result)[acc("FB Sales")]
		self.assertAlmostEqual(sales["fb_budget_aug_2026"], 3600)
		self.assertEqual(sales["aug_2026"], 3500)
		self.assertAlmostEqual(sales["fb_budget_total"], 12000)  # accumulated: last period

		q, _o = fx.run(pl, fx.filters(periodicity="Quarterly"))
		self.assertAlmostEqual(fx.rows_by_account(q)[acc("FB Sales")]["fb_budget_sep_2026"], 2400 + 1200 + 840)

		y, _o = fx.run(pl, fx.filters(periodicity="Yearly", budget_comparison="Total Only"))
		sales = fx.rows_by_account(y)[acc("FB Sales")]
		self.assertAlmostEqual(sales["fb_budget_total"], 12000)
		self.assertNotIn("fb_budget_jun_2027", [c["fieldname"] for c in y[0]])

	def test_multiple_fiscal_years_and_last_year(self):
		f = fx.filters(from_fiscal_year=fx.prev_fy(), periodicity="Yearly", show_last_year=1)
		result, original = fx.run(pl, f)
		fx.assert_actuals_unchanged(self, result, original)
		sales = fx.rows_by_account(result)[acc("FB Sales")]
		self.assertAlmostEqual(sales["fb_budget_jun_2026"], 6000)
		self.assertAlmostEqual(sales["fb_budget_jun_2027"], 12000)

		ly, _o = fx.run(pl, fx.filters(periodicity="Yearly", show_last_year=1))
		self.assertAlmostEqual(fx.rows_by_account(ly)[acc("FB Sales")]["fb_last_year"], 700)

	def test_ytd(self):
		f = fx.filters(filter_based_on="Date Range", period_start_date="2026-07-01", period_end_date="2026-08-31", show_ytd=1)
		result, _o = fx.run(pl, f)
		sales = fx.rows_by_account(result)[acc("FB Sales")]
		self.assertAlmostEqual(sales["fb_ytd_budget"], 3600)
		self.assertAlmostEqual(sales["fb_ytd_actual"], 3500)

	def test_cost_center_hierarchy_has_no_double_count(self):
		fx.budget([(acc("FB Rent"), 1200)], cost_center=f"FB Ops - {fx.ABBR}")
		fx.budget([(acc("FB Rent"), 600)], cost_center=f"FB Ops A - {fx.ABBR}")
		yearly = dict(periodicity="Yearly", show_zero_values=1)

		parent, _o = fx.run(pl, fx.filters(cost_center=[f"FB Ops - {fx.ABBR}"], **yearly))
		self.assertAlmostEqual(fx.rows_by_account(parent)[acc("FB Rent")]["fb_budget_jun_2027"], 1200)
		self.assertIn("parent", parent[2])

		child, _o = fx.run(pl, fx.filters(cost_center=[f"FB Ops A - {fx.ABBR}"], **yearly))
		self.assertAlmostEqual(fx.rows_by_account(child)[acc("FB Rent")]["fb_budget_jun_2027"], 600)

		company_wide, _o = fx.run(pl, fx.filters(**yearly))
		self.assertAlmostEqual(fx.rows_by_account(company_wide)[acc("FB Rent")]["fb_budget_jun_2027"], 2400 + 1200)

	def test_project_source_and_blocked_dimension(self):
		project = fx.project()
		fx.budget([(acc("FB Rent"), 500)], project_name=project)
		yearly = dict(periodicity="Yearly", show_zero_values=1)
		by_project, _o = fx.run(pl, fx.filters(project=[project], budget_source="Project", **yearly))
		self.assertAlmostEqual(fx.rows_by_account(by_project)[acc("FB Rent")]["fb_budget_jun_2027"], 500)

		blocked, original = fx.run(pl, fx.filters(project=[project], budget_source="Cost Center", **yearly))
		self.assertEqual(blocked[1], original[1])
		self.assertIn("cannot be filtered by Project", blocked[2])

	def test_duplicate_budget_lines_are_not_repeated(self):
		fx.budget([(acc("FB Rent"), 2400)], validate=False)  # bypasses ERPNext's duplicate check
		result, _o = fx.run(pl, fx.filters(periodicity="Yearly"))
		self.assertAlmostEqual(fx.rows_by_account(result)[acc("FB Rent")]["fb_budget_jun_2027"], 2400)
		self.assertIn("duplicate", result[2])

	def test_zero_budget_accounts_setting(self):
		fx.set_settings(display_zero_budget_accounts=1)
		try:
			result, original = fx.run(pl, fx.filters())
		finally:
			fx.set_settings(display_zero_budget_accounts=0)
		fx.assert_actuals_unchanged(self, result, original)
		services = fx.rows_by_account(result)[acc("FB Services")]
		self.assertEqual(services["fb_budget_only"], 1)
		self.assertEqual(services["jul_2026"], 0.0)
		self.assertAlmostEqual(services["fb_budget_jul_2026"], 100)
		accounts = [r.get("account") for r in result[1]]
		self.assertEqual(accounts.index(acc("FB Services")), accounts.index(acc("FB Sales")) + 1)

	def test_presentation_currency(self):
		if not frappe.db.exists("Currency Exchange", {"from_currency": "EGP", "to_currency": "USD", "date": "2026-06-30"}):
			frappe.get_doc(
				doctype="Currency Exchange", from_currency="EGP", to_currency="USD", exchange_rate=0.02, date="2026-06-30",
				for_buying=1, for_selling=1,
			).insert()
		result, _o = fx.run(pl, fx.filters(presentation_currency="USD"))
		self.assertAlmostEqual(fx.rows_by_account(result)[acc("FB Sales")]["fb_budget_jul_2026"], 48)

	def test_growth_view_is_untouched(self):
		result, original = fx.run(pl, fx.filters(selected_view="Growth"))
		self.assertEqual(result[1], original[1])
		self.assertEqual(result[0], original[0])

	def test_other_company_sees_no_budget(self):
		fx.setup_company("FB Test Co 2", "FBT2")
		f = fx.filters(company="FB Test Co 2", show_zero_values=1)
		result, original = fx.run(pl, f)
		fx.assert_actuals_unchanged(self, result, original)
		self.assertTrue(all(r.get("fb_budget_jul_2026") is None for r in result[1] if r))

	def test_through_report_api_and_exports(self):
		from frappe.desk.query_report import _export_query, generate_report_result, run

		f = fx.filters(periodicity="Yearly")
		data = run("Profit and Loss Statement", filters=frappe.as_json(f), ignore_prepared_report=True)
		self.assertIn("fb_budget_jun_2027", [c["fieldname"] for c in data["columns"]])

		# prepared reports and background exports go through generate_report_result
		prepared = generate_report_result(frappe.get_doc("Report", "Profit and Loss Statement"), filters=dict(f))
		self.assertIn("fb_budget_jun_2027", [c["fieldname"] for c in prepared["columns"]])

		for fmt in ("CSV", "Excel"):
			params = frappe._dict(
				report_name="Profit and Loss Statement",
				file_format_type=fmt,
				filters=dict(f),
				visible_idx=[],
				include_indentation=0,
			)
			_name, ext, content = _export_query(params, {"delimiter": ",", "quoting": 2}, populate_response=False)
			self.assertTrue(content)
			if fmt == "CSV":
				self.assertIn("Budget", content.decode())
