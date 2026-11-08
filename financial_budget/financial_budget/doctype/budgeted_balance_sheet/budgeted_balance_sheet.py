import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, formatdate, getdate

from financial_budget.financial_budget.budget_periods import allocate, fiscal_year_months

BALANCE_SHEET_ROOTS = ("Asset", "Liability", "Equity")


class BudgetedBalanceSheet(Document):
	def validate(self):
		self.currency = frappe.get_cached_value("Company", self.company, "default_currency")
		fy = frappe.get_cached_doc("Fiscal Year", self.fiscal_year)
		companies = [c.company for c in fy.get("companies", [])]
		if companies and self.company not in companies:
			frappe.throw(_("Fiscal Year {0} is not assigned to company {1}").format(self.fiscal_year, self.company))

		allowed_ends = (
			{e for _s, e in fiscal_year_months(fy.year_start_date, fy.year_end_date)}
			if self.planning_frequency == "Monthly"
			else {getdate(fy.year_end_date)}
		)
		seen = set()
		for row in self.accounts:
			acc = frappe.get_cached_value(
				"Account", row.account, ["company", "is_group", "root_type", "account_type"], as_dict=True
			)
			if acc.company != self.company:
				frappe.throw(_("Row {0}: Account {1} does not belong to company {2}").format(row.idx, row.account, self.company))
			if acc.is_group:
				frappe.throw(_("Row {0}: plan leaf accounts only; group totals are rolled up").format(row.idx))
			if acc.root_type not in BALANCE_SHEET_ROOTS:
				frappe.throw(_("Row {0}: {1} is not an Asset, Liability or Equity account").format(row.idx, row.account))
			end = getdate(row.period_end_date)
			if end not in allowed_ends:
				frappe.throw(
					_("Row {0}: Period End Date {1} must be a {2} period end of fiscal year {3}").format(
						row.idx, formatdate(end), _(self.planning_frequency), self.fiscal_year
					)
				)
			if (row.account, end) in seen:
				frappe.throw(_("Row {0}: {1} is planned twice for {2}").format(row.idx, row.account, formatdate(end)))
			seen.add((row.account, end))
			row.root_type, row.account_type, row.currency = acc.root_type, acc.account_type, self.currency
			row.planned_closing_balance = flt(row.opening_planned_balance) + flt(row.planned_movement)
		self.build_periods(fy)

	def build_periods(self, fy):
		"""One balance check per planned date; manual profit/loss values are kept."""
		manual = {getdate(p.period_end_date): flt(p.planned_profit_loss) for p in self.get("periods", [])}
		dates = sorted({getdate(r.period_end_date) for r in self.accounts})
		profit = (
			planned_profit_from_budget(self.company, fy, dates)
			if self.profit_loss_source == "ERPNext Budget"
			else {d: manual.get(d, 0.0) for d in dates}
		)
		precision = cint(frappe.db.get_default("currency_precision")) or 2
		self.set("periods", [])
		balanced = True
		for d in dates:
			rows = [r for r in self.accounts if getdate(r.period_end_date) == d]
			total = {rt: sum(flt(r.planned_closing_balance) for r in rows if r.root_type == rt) for rt in BALANCE_SHEET_ROOTS}
			difference = flt(total["Asset"] - total["Liability"] - total["Equity"] - profit[d], precision)
			balanced = balanced and difference == 0
			self.append(
				"periods",
				{
					"period_end_date": d,
					"planned_profit_loss": flt(profit[d], precision),
					"total_assets": total["Asset"],
					"total_liabilities": total["Liability"],
					"total_equity": total["Equity"],
					"difference": difference,
					"currency": self.currency,
				},
			)
		self.is_balanced = int(balanced)

	def before_submit(self):
		unbalanced = [p for p in self.periods if flt(p.difference)]
		if unbalanced:
			frappe.throw(
				_("Plan is not balanced (Assets = Liabilities + Equity + Planned Profit/Loss) for: {0}").format(
					", ".join(f"{formatdate(p.period_end_date)} ({p.difference})" for p in unbalanced)
				),
				title=_("Unbalanced Plan"),
			)
		duplicate = frappe.db.exists(
			"Budgeted Balance Sheet",
			{
				"company": self.company,
				"fiscal_year": self.fiscal_year,
				"scenario": self.scenario,
				"docstatus": 1,
				"name": ("!=", self.name),
			},
		)
		if duplicate:
			frappe.throw(
				_("{0} is already submitted for {1}, {2}, scenario {3}. Cancel it or use another scenario.").format(
					duplicate, self.company, self.fiscal_year, self.scenario
				)
			)


def planned_profit_from_budget(company, fy, dates):
	"""Year-to-date budgeted profit (income - expense) at each date from submitted Cost Center budgets."""
	from financial_budget.financial_budget.budget_reader import get_budget_segments

	segments, _notes = get_budget_segments(company, "Cost Center", fy.year_start_date, fy.year_end_date)
	if not segments:
		frappe.throw(
			_("No submitted Cost Center Budget found for {0} in {1}. Create one or set the source to Manual.").format(
				company, fy.name
			)
		)
	root_type = dict(
		frappe.get_all("Account", filters={"name": ("in", list(segments))}, fields=["name", "root_type"], as_list=True)
	)
	sign = {"Income": 1, "Expense": -1}
	return {
		d: sum(sign.get(root_type.get(a), 0) * allocate(s, fy.year_start_date, d) for a, s in segments.items())
		for d in dates
	}
