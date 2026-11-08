"""Profit and Loss Statement: ERPNext Budget vs actual."""

import frappe
from frappe import _
from frappe.utils import flt

from financial_budget.financial_budget import budget_variance as bv
from financial_budget.financial_budget import report_integration as ri
from financial_budget.financial_budget.budget_periods import allocate
from financial_budget.financial_budget.budget_reader import get_account_tree, get_budget_segments

ROOTS = (("Income", "Credit"), ("Expense", "Debit"))


def build(filters, result, original, opts):
	columns, data = result[0], result[1]
	if not data:
		return result
	if filters.get("selected_view") in ("Growth", "Margin"):
		return ri.with_message(result, _("Budget comparison is only available in Report View."))

	source = opts.budget_source
	blocked = ri.blocked_by_filters(filters, allowed=(frappe.scrub(source),))
	if blocked:
		return ri.with_message(
			result,
			_("Budget is not shown: {0} budgets cannot be filtered by {1}. Change Budget Source or remove the filter.").format(
				_(source), _(frappe.unscrub(blocked))
			),
		)

	company = filters.company
	period_list = ri.period_list_for(filters)
	layer = bv.Layer(period_list, filters.accumulated_values)
	range_start, range_end = period_list[0]["year_start_date"], period_list[-1]["to_date"]

	rate = ri.conversion_rate(filters, frappe.get_cached_value("Company", company, "default_currency"), range_end)
	if rate is None:
		return ri.with_message(result, _("Budget is not shown: no exchange rate to the presentation currency."))

	accounts = get_account_tree(company, [r for r, _b in ROOTS])
	segments, notes = get_budget_segments(company, source, range_start, range_end, budget_scope(filters, source))
	layer.notes.extend(notes)
	segments = {a: s for a, s in segments.items() if a in accounts}
	actuals = {r["account"]: bv.actual_total(layer, r) for r in data if r and r.get("account") in accounts}
	segments = {
		a: [(s, e, -amount) for s, e, amount in segs] if is_contra(accounts[a], actuals.get(a)) else segs
		for a, segs in segments.items()
	}

	def leaf_amounts(start_of, keys_periods):
		return {
			a: {key: allocate(segs, start_of(p), p.to_date) * rate for key, p in keys_periods}
			for a, segs in segments.items()
		}

	keyed = [(p.key, p) for p in period_list]
	start_of = (lambda p: range_start) if layer.accumulated else (lambda p: p.from_date)
	tree = bv.rollup(accounts, leaf_amounts(start_of, keyed), layer.keys)

	totals = apply_account_tree(data, layer, accounts, tree, set(segments), ROOTS, opts, period_list, company)
	income, expense = totals.get("Income"), totals.get("Expense")
	net_label = "'" + _("Profit for the year") + "'"
	net = None
	if income is not None and expense is not None:
		net = {k: income[k] - expense[k] for k in layer.keys}
	for idx, row in enumerate(data):
		if row and row.get("account") == net_label:
			missing = [_(r) for r, t in (("Income", income), ("Expense", expense)) if t is None]
			layer.set(idx, net, _("Plan Incomplete: {0} budget missing").format(", ".join(missing)) if missing else None)

	end = period_list[-1].to_date
	if opts.show_ytd:
		from erpnext.accounts.utils import get_fiscal_year

		fy_start = get_fiscal_year(end, company=company)[1]
		ytd_tree = bv.rollup(
			accounts,
			{a: {"ytd": allocate(s, fy_start, end) * rate} for a, s in segments.items()},
			["ytd"],
		)
		ytd_actual = ri.run_original(
			original, filters, **_single_range(fy_start, end)
		)
		_fill_extra(data, layer, accounts, ytd_tree, "fb_ytd_budget", ROOTS, net_label)
		_fill_actual(data, layer, ytd_actual, "fb_ytd_actual", net_label)

	if opts.show_last_year:
		last_year = ri.run_original(
			original,
			filters,
			**_single_range(ri.shift_year(period_list[0].from_date), ri.shift_year(end)),
		)
		if last_year is None:
			layer.notes.append(_("Last Year is not available (no fiscal year or data for the previous year)."))
		_fill_actual(data, layer, last_year, "fb_last_year", net_label)

	out = list(result)
	out[0] = bv.build_columns(columns, layer, opts)
	out[1] = bv.write(data, layer)
	if opts.charts and len(out) > 3 and net:
		out[3] = bv.add_chart_dataset(out[3], _("Budget Net Profit/Loss"), [net[k] for k in layer.keys])
	if len(out) > 4 and isinstance(out[4], list):
		out[4] = _summary(out[4], layer, income, expense, accounts, segments, filters, rate, start_of, period_list)
	out = tuple(out) if isinstance(result, tuple) else out
	return ri.with_message(out, "<br>".join(layer.notes))


def is_contra(account, actual):
	"""A deduction inside its section (scholarships or discounts under Income, recoveries under Expense).
	ERPNext budgets are entered as positive amounts, so its budget is shown negative like its actual.
	Balance Must Be decides when set; otherwise a negative actual in the report range does."""
	natural = "Credit" if account.root_type == "Income" else "Debit"
	if account.balance_must_be:
		return account.balance_must_be != natural
	return flt(actual) < 0


def budget_scope(filters, source):
	if source == "Cost Center" and filters.get("cost_center"):
		from erpnext.accounts.report.financial_statements import get_cost_centers_with_children

		return get_cost_centers_with_children(filters.cost_center)
	if source == "Project" and filters.get("project"):
		project = filters.project
		return frappe.parse_json(project) if isinstance(project, str) and project.startswith("[") else (
			project if isinstance(project, list) else [project]
		)
	return None


def apply_account_tree(
	data, layer, accounts, tree, budgeted_leaves, roots, opts, period_list, company, missing_status=None, partial_status=None
):
	"""Budget for account rows and the section total rows; status for missing budgets.
	Returns {root_type: per-period totals or None}."""
	missing_status = missing_status or _("No Budget")
	partial_status = partial_status or _("Partial Budget")
	visible_with_value = {
		r["account"] for r in data if r and r.get("account") in accounts and r.get("has_value")
	}
	missing = [
		accounts[a] for a in visible_with_value if not accounts[a].is_group and a not in budgeted_leaves
	]

	def status(acc):
		inside = [m for m in missing if acc.lft <= m.lft and m.rgt <= acc.rgt]
		if not inside:
			return None
		return partial_status if tree.get(acc.name) and acc.is_group else missing_status

	totals = {}
	for root_type, balance in roots:
		root_accounts = [a for a in accounts.values() if a.root_type == root_type and not a.parent_account]
		budgeted = [tree[a.name] for a in root_accounts if a.name in tree]
		totals[root_type] = (
			{k: sum(t.get(k, 0.0) for t in budgeted) for k in layer.keys} if budgeted else None
		)
		label = "'" + _("Total {0} ({1})").format(_(root_type), _(balance)) + "'"
		for idx, row in enumerate(data):
			if row and row.get("account") == label:
				if root_type == "Expense":
					layer.cost_rows.add(idx)
				gaps = any(m.root_type == root_type for m in missing)
				layer.set(idx, totals[root_type], (partial_status if budgeted else missing_status) if gaps else None)

	for idx, row in enumerate(data):
		acc = accounts.get(row.get("account")) if row else None
		if acc:
			layer.set(idx, tree.get(acc.name), status(acc))
			if acc.root_type == "Expense":
				layer.cost_rows.add(idx)

	if opts.zero_budget_accounts:
		insert_budget_only_rows(data, layer, accounts, tree, budgeted_leaves, roots, period_list, company)
	return totals


def insert_budget_only_rows(data, layer, accounts, tree, budgeted_leaves, roots, period_list, company):
	"""Show budgeted accounts that ERPNext hid because their actual is zero, in tree order."""
	from erpnext.accounts.report.financial_statements import filter_accounts, get_accounts

	index_of = {r["account"]: i for i, r in enumerate(data) if r and r.get("account") in accounts}
	for root_type, _balance in roots:
		block = [i for a, i in index_of.items() if accounts[a].root_type == root_type]
		if not block:
			continue  # shortcut: a root type with no actual rows at all gets no tree, add it if users ask
		needed = set()
		for leaf in budgeted_leaves:
			acc = accounts.get(leaf)
			if acc and acc.root_type == root_type and leaf not in index_of:
				while acc and acc.name not in index_of and acc.name not in needed:
					needed.add(acc.name)
					acc = accounts.get(acc.parent_account)
		if not needed:
			continue
		ordered, _by_name, _children = filter_accounts(get_accounts(company, root_type))
		after_block = max(block) + 1
		pending = []
		for acc in ordered:
			if acc.name in index_of:
				for row in pending:
					layer.inserted.append((index_of[acc.name], row))
				pending = []
			elif acc.name in needed:
				pending.append(_budget_only_row(acc, period_list, tree.get(acc.name), data[block[0]]))
		for row in pending:
			layer.inserted.append((after_block, row))


def _budget_only_row(acc, period_list, budget, sample):
	row = frappe._dict(
		account=acc.name,
		parent_account=acc.parent_account or "",
		indent=flt(acc.indent),
		year_start_date=sample.get("year_start_date"),
		year_end_date=sample.get("year_end_date"),
		currency=sample.get("currency"),
		include_in_gross=acc.include_in_gross,
		account_type=acc.account_type,
		is_group=acc.is_group,
		opening_balance=0.0,
		account_name=f"{acc.account_number} - {acc.account_name}" if acc.account_number else acc.account_name,
		has_value=False,
		total=0.0,
		fb_budget_only=1,
		_fb_cost=acc.root_type == "Expense",
		fb_status=_("Budget Only (no actual)"),
		_fb_budget=budget,
	)
	for p in period_list:
		row[p.key] = 0.0
	return row


def _single_range(start, end):
	return dict(
		filter_based_on="Date Range",
		period_start_date=start,
		period_end_date=end,
		periodicity="Yearly",
		accumulated_values=0,
		show_zero_values=1,
		selected_view="Report",
	)


def _fill_extra(data, layer, accounts, tree, field, roots, net_label):
	totals = {}
	for root_type, balance in roots:
		vals = [tree[a.name]["ytd"] for a in accounts.values() if a.root_type == root_type and not a.parent_account and a.name in tree]
		totals["'" + _("Total {0} ({1})").format(_(root_type), _(balance)) + "'"] = sum(vals) if vals else None
	inc, exp = list(totals.values())
	totals[net_label] = inc - exp if inc is not None and exp is not None else None
	for idx, row in enumerate(data):
		if not row:
			continue
		key = row.get("account")
		value = tree.get(key, {}).get("ytd") if key in accounts else totals.get(key)
		layer.extra.setdefault(idx, {})[field] = None if value is None else flt(value, bv.precision())


def _fill_actual(data, layer, rows, field, net_label):
	for idx, row in enumerate(data):
		if not row or not row.get("account"):
			continue
		if rows is None:
			value = None
		else:
			other = rows.get(row["account"])
			value = flt(other.get("total")) if other else 0.0
		layer.extra.setdefault(idx, {})[field] = value


def _summary(summary, layer, income, expense, accounts, segments, filters, rate, start_of, period_list):
	"""Append budget cards (Gross Profit only when accounts are flagged Include in Gross)."""
	cards = []
	currency = filters.presentation_currency or frappe.get_cached_value("Company", filters.company, "default_currency")
	if income is not None and expense is not None:
		net_total = bv.total_of(layer, {k: income[k] - expense[k] for k in layer.keys})
		cards.append({"value": net_total, "label": _("Budget Net Profit"), "datatype": "Currency", "currency": currency})
	gross = [a for a in segments if accounts[a].include_in_gross]
	if gross:
		value = 0.0
		for a in gross:
			amount = bv.total_of(
				layer, {p.key: allocate(segments[a], start_of(p), p.to_date) * rate for p in period_list}
			)
			value += amount if accounts[a].root_type == "Income" else -amount
		cards.append({"value": value, "label": _("Budget Gross Profit"), "datatype": "Currency", "currency": currency})
	if not cards:
		return summary
	return [*summary, {"type": "separator", "value": "|"}, *cards]
