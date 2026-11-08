"""Balance Sheet: submitted Budgeted Balance Sheet (planned closing balances) vs actual."""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, formatdate

from financial_budget.financial_budget import budget_variance as bv
from financial_budget.financial_budget import report_integration as ri
from financial_budget.financial_budget.budget_reader import PlanIndex, get_account_tree
from financial_budget.financial_budget.profit_and_loss import _fill_actual, _single_range, apply_account_tree

ROOTS = (("Asset", "Debit"), ("Liability", "Credit"), ("Equity", "Credit"))


def build(filters, result, original, opts):
	columns, data = result[0], result[1]
	if not data:
		return result
	if not cint(filters.accumulated_values):
		return ri.with_message(result, _("Budget comparison needs Accumulated Values (balances)."))
	if filters.get("selected_view") == "Growth":
		return ri.with_message(result, _("Budget comparison is only available in Report View."))
	blocked = ri.blocked_by_filters(filters)
	if blocked:
		return ri.with_message(
			result,
			_("Budget is not shown: the balance sheet plan is company-level and cannot be filtered by {0}.").format(
				_(frappe.unscrub(blocked))
			),
		)

	company = filters.company
	period_list = ri.period_list_for(filters)
	layer = bv.Layer(period_list, 1)
	end = period_list[-1].to_date
	rate = ri.conversion_rate(filters, frappe.get_cached_value("Company", company, "default_currency"), end)
	if rate is None:
		return ri.with_message(result, _("Budget is not shown: no exchange rate to the presentation currency."))

	plan = PlanIndex(company, opts.scenario)
	accounts = get_account_tree(company, [r for r, _b in ROOTS])
	planned_keys = [p.key for p in period_list if plan.balances(p.to_date) is not None]
	unplanned = [p for p in period_list if p.key not in planned_keys]
	if unplanned:
		layer.notes.append(
			_("Plan Incomplete: no submitted Budgeted Balance Sheet (scenario {0}) for {1}.").format(
				frappe.bold(opts.scenario), ", ".join(formatdate(p.to_date) for p in unplanned)
			)
		)
	if not planned_keys:
		return ri.with_message(result, "<br>".join(layer.notes))

	leaf, planned_sets = {}, []
	for p in period_list:
		balances = plan.balances(p.to_date)
		if balances is None:
			continue
		planned = {a for a in balances if a in accounts and not accounts[a].is_group}
		planned_sets.append(planned)
		for a in planned:
			leaf.setdefault(a, {})[p.key] = balances[a] * rate
	# a leaf counts as planned only if every planned period has a balance for it
	complete_leaves = set.intersection(*planned_sets) if planned_sets else set()

	tree = bv.rollup(accounts, leaf, layer.keys)
	totals = apply_account_tree(
		data, layer, accounts, tree, complete_leaves, ROOTS, opts, period_list, company, _("Not Planned"), _("Plan Incomplete")
	)

	profit_loss = {p.key: plan.profit_loss.get(p.to_date) for p in period_list}
	profit_loss = {k: (v * rate if v is not None else None) for k, v in profit_loss.items()}
	has_actual = {accounts[r["account"]].root_type for r in data if r and r.get("account") in accounts}

	def side(root_type, key):
		# an unplanned side is missing if it has actual balances, a real zero if it has none
		if totals.get(root_type) is None:
			return None if root_type in has_actual else 0.0
		return totals[root_type].get(key, 0.0)

	credit_side = {k: _sum(side("Liability", k), side("Equity", k), profit_loss.get(k)) for k in layer.keys}
	labels = {
		"'" + _("Provisional Profit / Loss (Credit)") + "'": (profit_loss, None),
		"'" + _("Total (Credit)") + "'": (credit_side, None),
		"'" + _("Unclosed Fiscal Years Profit / Loss (Credit)") + "'": (None, _("Not part of the plan")),
	}
	for idx, row in enumerate(data):
		if row and row.get("account") in labels:
			values, status = labels[row["account"]]
			layer.set(idx, values, status)

	# periods without a plan are missing, never zero
	for values in [*tree.values(), *(t for t in totals.values() if t)]:
		for p in unplanned:
			values[p.key] = None

	if opts.show_last_year:
		ly_end = ri.shift_year(end)
		last_year = ri.run_original(
			original, filters, **(_single_range(add_days(add_months(ly_end, -12), 1), ly_end) | {"accumulated_values": 1})
		)
		if last_year is None:
			layer.notes.append(_("Last Year is not available (no fiscal year or data for the previous year)."))
		_fill_actual(data, layer, last_year, "fb_last_year", None)

	out = list(result)
	out[0] = bv.build_columns(columns, layer, opts)
	out[1] = bv.write(data, layer)
	if opts.charts and len(out) > 3 and totals.get("Asset"):
		out[3] = bv.add_chart_dataset(out[3], _("Budget Assets"), [totals["Asset"][k] for k in layer.keys])
	out = tuple(out) if isinstance(result, tuple) else out
	return ri.with_message(out, "<br>".join(layer.notes))


def _sum(*values):
	return None if any(v is None for v in values) else sum(values)
