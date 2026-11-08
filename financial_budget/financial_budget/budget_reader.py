"""Bulk readers for budgets, plans and the account tree (read-only, no GL access)."""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate

from financial_budget.financial_budget.budget_periods import budget_segments


def get_fiscal_years(company, from_date, to_date):
	"""Enabled fiscal years of the company that overlap [from_date, to_date]."""
	fy = frappe.qb.DocType("Fiscal Year")
	fyc = frappe.qb.DocType("Fiscal Year Company")
	rows = (
		frappe.qb.from_(fy)
		.left_join(fyc)
		.on(fyc.parent == fy.name)
		.select(fy.name, fy.year_start_date, fy.year_end_date, fyc.company)
		.where((fy.disabled == 0) & (fy.year_start_date <= to_date) & (fy.year_end_date >= from_date))
		.run(as_dict=True)
	)
	with_company = {r.name for r in rows if r.company}
	out = {}
	for r in rows:
		if r.company == company or (r.name not in with_company):
			out[r.name] = r
	return sorted(out.values(), key=lambda r: r.year_start_date)


def get_account_tree(company, root_types):
	"""{name: account} for the company's accounts of the given root types."""
	return {
		a.name: a
		for a in frappe.get_all(
			"Account",
			filters={"company": company, "root_type": ("in", root_types)},
			fields=[
				"name",
				"parent_account",
				"lft",
				"rgt",
				"is_group",
				"root_type",
				"account_type",
				"include_in_gross",
				"balance_must_be",
			],
			order_by="lft",
		)
	}


def get_budget_segments(company, budget_against, from_date, to_date, scope=None, account_types=None):
	"""Dated budget segments per leaf account from submitted ERPNext Budgets.

	budget_against: "Cost Center" / "Project" (Budget.budget_against).
	scope: list of dimension values to include (already expanded to children), or None = all.
	Overlap rule: for tree dimensions (Cost Center), when a budget exists on a node and on one
	of its ancestors for the same account and fiscal year, only the ancestor budget is used.
	ERPNext treats a parent budget as the limit for its whole subtree, so adding the child
	would double count.

	Returns (segments: {account: [(from, to, amount)]}, notes: [str]).
	"""
	fiscal_years = {fy.name: fy for fy in get_fiscal_years(company, from_date, to_date)}
	if not fiscal_years:
		return {}, []

	field = frappe.scrub(budget_against)
	b = frappe.qb.DocType("Budget")
	ba = frappe.qb.DocType("Budget Account")
	query = (
		frappe.qb.from_(b)
		.join(ba)
		.on(ba.parent == b.name)
		.select(
			b.name.as_("budget"),
			getattr(b, field).as_("dimension"),
			b.fiscal_year,
			b.monthly_distribution,
			ba.account,
			ba.budget_amount,
		)
		.where(
			(b.docstatus == 1)
			& (b.company == company)
			& (b.budget_against == budget_against)
			& (b.fiscal_year.isin(list(fiscal_years)))
			& (ba.parenttype == "Budget")
		)
		.orderby(b.name)
		.orderby(ba.idx)
	)
	if scope is not None:
		if not scope:
			return {}, []
		query = query.where(getattr(b, field).isin(list(scope)))
	rows = query.run(as_dict=True)

	if account_types:
		allowed = set(
			frappe.get_all(
				"Account",
				filters={"company": company, "account_type": ("in", account_types)},
				pluck="name",
			)
		)
		rows = [r for r in rows if r.account in allowed]

	notes = []
	# one entry per (dimension, account, fiscal year); ERPNext validates this, imports may not
	unique, duplicates = {}, 0
	for r in rows:
		key = (r.dimension, r.account, r.fiscal_year)
		if key in unique:
			duplicates += 1
			continue
		unique[key] = r
	if duplicates:
		notes.append(
			_("{0} duplicate budget line(s) for the same dimension, account and fiscal year were ignored.").format(
				duplicates
			)
		)
	rows = list(unique.values())

	if frappe.get_meta(budget_against).is_tree and rows:
		rows, skipped = _drop_overlapping_descendants(budget_against, rows)
		if skipped:
			notes.append(
				_(
					"{0} budget line(s) on child {1}s were not added because a parent {1} has a budget for the same account (no double counting)."
				).format(skipped, _(budget_against))
			)

	distributions = _get_distributions({r.monthly_distribution for r in rows if r.monthly_distribution})
	segments = {}
	for r in rows:
		fy = fiscal_years[r.fiscal_year]
		segments.setdefault(r.account, []).extend(
			budget_segments(
				flt(r.budget_amount),
				fy.year_start_date,
				fy.year_end_date,
				distributions.get(r.monthly_distribution),
			)
		)
	return segments, notes


def _drop_overlapping_descendants(doctype, rows):
	nodes = {
		d.name: d
		for d in frappe.get_all(
			doctype, filters={"name": ("in", list({r.dimension for r in rows}))}, fields=["name", "lft", "rgt"]
		)
	}
	by_key = {}
	for r in rows:
		by_key.setdefault((r.account, r.fiscal_year), []).append(r)

	kept, skipped = [], 0
	for group in by_key.values():
		for r in group:
			me = nodes.get(r.dimension)
			covered = me and any(
				o is not r and (n := nodes.get(o.dimension)) and n.lft < me.lft and n.rgt > me.rgt for o in group
			)
			if covered:
				skipped += 1
			else:
				kept.append(r)
	return kept, skipped


def _get_distributions(names):
	if not names:
		return {}
	out = {}
	for d in frappe.get_all(
		"Monthly Distribution Percentage",
		filters={"parent": ("in", list(names)), "parenttype": "Monthly Distribution"},
		fields=["parent", "month", "percentage_allocation"],
	):
		out.setdefault(d.parent, {})[d.month] = flt(d.percentage_allocation)
	return out


class PlanIndex:
	"""Submitted Budgeted Balance Sheet data of one company + scenario.

	balance(account, date): planned closing balance at `date` if a plan period ends on that
	date, else the planned opening balance of the plan period that starts the day after.
	Balances are in natural sign (debit for assets, credit for liabilities/equity).
	"""

	def __init__(self, company, scenario):
		self.closing, self.opening_by_start, self.profit_loss = {}, {}, {}
		plans = frappe.get_all(
			"Budgeted Balance Sheet",
			filters={"company": company, "scenario": scenario, "docstatus": 1},
			fields=["name", "fiscal_year"],
		)
		if not plans:
			return
		fy_start = dict(
			frappe.get_all(
				"Fiscal Year",
				filters={"name": ("in", [p.fiscal_year for p in plans])},
				fields=["name", "year_start_date"],
				as_list=True,
			)
		)
		plan_fy = {p.name: p.fiscal_year for p in plans}
		rows = frappe.get_all(
			"Budgeted Balance Sheet Account",
			filters={"parent": ("in", list(plan_fy)), "parenttype": "Budgeted Balance Sheet"},
			fields=["parent", "period_end_date", "account", "opening_planned_balance", "planned_closing_balance"],
		)
		ends_by_plan = {}
		for r in rows:
			ends_by_plan.setdefault(r.parent, set()).add(getdate(r.period_end_date))
		start_of = {}
		for plan, ends in ends_by_plan.items():
			prev = getdate(fy_start[plan_fy[plan]])
			for end in sorted(ends):
				start_of[(plan, end)] = prev
				prev = add_days(end, 1)
		for r in rows:
			end = getdate(r.period_end_date)
			self.closing.setdefault(end, {})[r.account] = flt(r.planned_closing_balance)
			self.opening_by_start.setdefault(start_of[(r.parent, end)], {})[r.account] = flt(
				r.opening_planned_balance
			)
		for p in frappe.get_all(
			"Budgeted Balance Sheet Period",
			filters={"parent": ("in", list(plan_fy)), "parenttype": "Budgeted Balance Sheet"},
			fields=["period_end_date", "planned_profit_loss"],
		):
			self.profit_loss[getdate(p.period_end_date)] = flt(p.planned_profit_loss)

	def has_date(self, date):
		date = getdate(date)
		return date in self.closing or add_days(date, 1) in self.opening_by_start

	def balances(self, date):
		"""{account: balance} at date, or None when the plan has no balance for that date."""
		date = getdate(date)
		if date in self.closing:
			return self.closing[date]
		return self.opening_by_start.get(add_days(date, 1))


def get_cash_flow_adjustments(company, scenario, from_date, to_date):
	return frappe.get_all(
		"Cash Flow Planning Adjustment",
		filters={
			"company": company,
			"scenario": scenario,
			"docstatus": 1,
			"posting_date": ("between", [from_date, to_date]),
		},
		fields=["posting_date", "section", "amount"],
	)
