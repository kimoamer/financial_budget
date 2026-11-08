"""Fresh test data: a company with its own small account tree, cost centers, fiscal years,
journal entries, budgets and plans. Everything runs inside the FrappeTestCase transaction
and is rolled back; nothing existing on the site is read or changed."""

import copy

import frappe
from frappe.utils import getdate

COMPANY = "FB Test Co"
ABBR = "FBT"
FY = "2026-2027"  # 2026-07-01 .. 2027-06-30, a July fiscal year
PREV_FY = "FB 2025-2026"


def acc(name):
	return f"{name} - {ABBR}"


def setup_company(company=COMPANY, abbr=ABBR):
	if not frappe.db.exists("Company", company):
		frappe.get_doc(
			doctype="Company",
			company_name=company,
			abbr=abbr,
			default_currency="EGP",
			country="Egypt",
			create_chart_of_accounts_based_on="Standard Template",
			chart_of_accounts="Standard",
		).insert()
	_fiscal_years()
	if company != COMPANY:
		return
	roots = {
		r.root_type: r.name
		for r in frappe.get_all("Account", filters={"company": company, "parent_account": ("is", "not set")}, fields=["name", "root_type"])
	}
	tree = [
		("FB Operating Income", roots["Income"], 1, None),
		("FB Sales", acc("FB Operating Income"), 0, None),
		("FB Services", acc("FB Operating Income"), 0, None),
		("FB Operating Expenses", roots["Expense"], 1, None),
		("FB Rent", acc("FB Operating Expenses"), 0, None),
		("FB Salaries", acc("FB Operating Expenses"), 0, None),
		("FB Depreciation", acc("FB Operating Expenses"), 0, "Depreciation"),
		("FB Current Assets", roots["Asset"], 1, None),
		("FB Cash", acc("FB Current Assets"), 0, "Cash"),
		("FB Debtors", acc("FB Current Assets"), 0, "Receivable"),
		("FB Fixed Assets", roots["Asset"], 1, None),
		("FB Equipment", acc("FB Fixed Assets"), 0, "Fixed Asset"),
		("FB Acc Depreciation", acc("FB Fixed Assets"), 0, "Accumulated Depreciation"),
		("FB Liabilities", roots["Liability"], 1, None),
		("FB Creditors", acc("FB Liabilities"), 0, "Payable"),
		("FB Loan", acc("FB Liabilities"), 0, None),
		("FB Equity", roots["Equity"], 1, None),
		("FB Capital", acc("FB Equity"), 0, "Equity"),
	]
	for name, parent, is_group, account_type in tree:
		if not frappe.db.exists("Account", acc(name)):
			frappe.get_doc(
				doctype="Account",
				account_name=name,
				parent_account=parent,
				company=company,
				is_group=is_group,
				account_type=account_type,
			).insert()

	root_cc = frappe.db.get_value("Cost Center", {"company": company, "is_group": 1, "parent_cost_center": ("is", "not set")})
	for name, parent, is_group in (
		("FB Ops", root_cc, 1),
		("FB Ops A", f"FB Ops - {ABBR}", 0),
		("FB Ops B", f"FB Ops - {ABBR}", 0),
	):
		if not frappe.db.exists("Cost Center", f"{name} - {ABBR}"):
			frappe.get_doc(
				doctype="Cost Center", cost_center_name=name, parent_cost_center=parent, company=company, is_group=is_group
			).insert()
	if not frappe.db.exists("Project", {"project_name": "FB Project X"}):
		frappe.get_doc(doctype="Project", project_name="FB Project X", company=company).insert()


def project():
	return frappe.db.get_value("Project", {"project_name": "FB Project X"})


def main_cc(company=COMPANY):
	return frappe.get_cached_value("Company", company, "cost_center")


def _fiscal_years():
	if not frappe.db.exists("Fiscal Year", FY):
		frappe.get_doc(doctype="Fiscal Year", year=FY, year_start_date="2026-07-01", year_end_date="2027-06-30").insert()
	if not frappe.db.exists("Fiscal Year", {"year_start_date": "2025-07-01"}):
		frappe.get_doc(doctype="Fiscal Year", year=PREV_FY, year_start_date="2025-07-01", year_end_date="2026-06-30").insert()


def prev_fy():
	return frappe.db.get_value("Fiscal Year", {"year_start_date": "2025-07-01"})


def journal(date, lines, company=COMPANY):
	"""lines: [(account, debit, credit, extra dict)]"""
	je = frappe.get_doc(doctype="Journal Entry", company=company, posting_date=date, voucher_type="Journal Entry")
	for account, debit, credit, *extra in lines:
		row = {"account": account, "debit_in_account_currency": debit, "credit_in_account_currency": credit}
		row["cost_center"] = main_cc(company)
		if extra:
			row.update(extra[0])
		je.append("accounts", row)
	je.insert()
	je.submit()
	return je


def budget(lines, cost_center=None, project_name=None, fiscal_year=FY, distribution=None, company=COMPANY, validate=True):
	doc = frappe.get_doc(
		doctype="Budget",
		company=company,
		budget_against="Project" if project_name else "Cost Center",
		cost_center=None if project_name else (cost_center or main_cc(company)),
		project=project_name,
		fiscal_year=fiscal_year,
		monthly_distribution=distribution,
		applicable_on_booking_actual_expenses=0,
		accounts=[{"account": a, "budget_amount": amt} for a, amt in lines],
	)
	if not validate:
		doc.flags.ignore_validate = True
	doc.insert()
	doc.submit()
	return doc


def distribution(name, percentages):
	"""percentages: {"July": 20, ...}; the rest of the 12 months share the remainder equally."""
	months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
	rest = [m for m in months if m not in percentages]
	share = (100 - sum(percentages.values())) / len(rest)
	if frappe.db.exists("Monthly Distribution", name):
		return name
	doc = frappe.get_doc(
		doctype="Monthly Distribution",
		distribution_id=name,
		percentages=[{"month": m, "percentage_allocation": percentages.get(m, share)} for m in months],
	)
	doc.insert()
	return doc.name


def plan(rows, profit_loss_source="ERPNext Budget", scenario="Base", frequency="Annual", submit=True, periods=None, company=COMPANY):
	"""rows: [(period_end_date, account, opening, movement)]"""
	doc = frappe.get_doc(
		doctype="Budgeted Balance Sheet",
		company=company,
		fiscal_year=FY,
		scenario=scenario,
		planning_frequency=frequency,
		profit_loss_source=profit_loss_source,
		accounts=[
			{"period_end_date": d, "account": a, "opening_planned_balance": o, "planned_movement": m} for d, a, o, m in rows
		],
		periods=[{"period_end_date": d, "planned_profit_loss": v} for d, v in (periods or {}).items()],
	)
	doc.insert()
	if submit:
		doc.submit()
	return doc


def filters(**kw):
	f = frappe._dict(
		company=COMPANY,
		filter_based_on="Fiscal Year",
		from_fiscal_year=FY,
		to_fiscal_year=FY,
		period_start_date="2026-07-01",
		period_end_date="2027-06-30",
		periodicity="Monthly",
		accumulated_values=0,
		include_default_book_entries=1,
		selected_view="Report",
		show_budget=1,
		show_variance=1,
		show_variance_pct=1,
		budget_comparison="Per Period",
	)
	f.update(kw)
	return f


def rows_by_account(result, key="account"):
	return {r.get(key): r for r in result[1] if r and r.get(key)}


def set_settings(**values):
	s = frappe.get_single("Financial Budget Settings")
	s.update(values)
	s.save()


def run(module, f):
	"""Run (wrapped, original) on deep copies of the same filters."""
	from financial_budget.patches import financial_reports

	financial_reports.apply()
	return module.execute(copy.deepcopy(f)), module.execute.__wrapped__(copy.deepcopy(f))


def assert_actuals_unchanged(test, enhanced, original):
	"""Every original row is present, in order, with every original key/value unchanged."""
	originals = [r for r in original[1]]
	matched = [r for r in enhanced[1] if not (r and r.get("fb_budget_only"))]
	test.assertEqual(len(matched), len(originals))
	for new, old in zip(matched, originals, strict=True):
		for key, value in old.items():
			test.assertEqual(new.get(key), value, f"{key} changed in row {old.get('account') or old.get('section')}")
	original_fields = [c["fieldname"] for c in original[0]]
	test.assertEqual([c["fieldname"] for c in enhanced[0] if not c["fieldname"].startswith("fb_")], original_fields)


def d(s):
	return getdate(s)


class BudgetTestCase:
	"""Mixin: each test runs in its own savepoint so budgets created by one test never leak."""

	def setUp(self):
		frappe.db.savepoint("fb_test")

	def tearDown(self):
		frappe.db.rollback(save_point="fb_test")
