"""Cash Flow (indirect method): planned cash flow derived from the balance sheet plan.

Per report period [S, T] (S follows ERPNext's get_start_date):
- Profit for the year   = planned P&L YTD(T) - YTD(S-1)          (Budgeted Balance Sheet periods)
- Depreciation          = ERPNext Budget on Depreciation accounts  (added back, like ERPNext)
- Receivable/Payable/Stock/Fixed Asset/Equity lines
                        = cash effect of the planned balance change of those accounts
                          (asset increase = outflow, liability/equity increase = inflow)
- Planned Adjustments   = submitted Cash Flow Planning Adjustments (movements no standard line covers)
Reconciliation: planned opening cash + planned net change must equal the planned cash/bank
balance at T; any difference is shown, never forced to zero.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, formatdate, getdate

from financial_budget.financial_budget import budget_variance as bv
from financial_budget.financial_budget import report_integration as ri
from financial_budget.financial_budget.budget_periods import allocate
from financial_budget.financial_budget.budget_reader import (
	PlanIndex,
	get_account_tree,
	get_budget_segments,
	get_cash_flow_adjustments,
)
from financial_budget.financial_budget.profit_and_loss import _single_range

CASH_TYPES = ("Cash", "Bank")
# account types whose movement a standard Cash Flow line already reports
DERIVED_TYPES = ("Depreciation", "Accumulated Depreciation", "Receivable", "Payable", "Stock", "Fixed Asset", "Equity")


def build(filters, result, original, opts):
	from erpnext.accounts.report.cash_flow.cash_flow import get_cash_flow_accounts, get_start_date
	from erpnext.accounts.utils import get_fiscal_year

	columns, data = result[0], result[1]
	if not data:
		return result
	blocked = ri.blocked_by_filters(filters)
	if blocked:
		return ri.with_message(
			result,
			_("Budget is not shown: the cash flow plan is company-level and cannot be filtered by {0}.").format(
				_(frappe.unscrub(blocked))
			),
		)

	company = filters.company
	period_list = ri.period_list_for(filters)
	layer = bv.Layer(period_list, filters.accumulated_values)
	sections = get_cash_flow_accounts()
	plan = PlanIndex(company, opts.scenario)
	accounts = get_account_tree(company, ["Asset", "Liability", "Equity"])
	leaves_of = {}
	for a in accounts.values():
		if not a.is_group and a.account_type:
			leaves_of.setdefault(a.account_type, []).append(a)

	starts = {p.key: getdate(get_start_date(p, layer.accumulated, company)) for p in period_list}
	first, end = min(starts.values()), period_list[-1].to_date
	dep_segments, notes = get_budget_segments(company, "Cost Center", first, end, account_types=["Depreciation"])
	layer.notes.extend(notes)
	adjustments = get_cash_flow_adjustments(company, opts.scenario, first, end)

	rows = _index_rows(data, sections)
	values = {}  # (kind, name) -> {key: amount | None}
	missing = {}  # (kind, name) -> set of component labels
	recon = {name: {} for name in ("opening", "net", "closing", "planned", "difference")}

	def put(kind, name, key, amount, component=None):
		values.setdefault((kind, name), {})[key] = amount
		if amount is None and component:
			missing.setdefault((kind, name), set()).add(component)

	for p in period_list:
		key, start, to = p.key, starts[p.key], p.to_date
		before = add_days(start, -1)
		bal_end, bal_start = plan.balances(to), plan.balances(before)
		no_plan = bal_end is None or bal_start is None

		for section in sections:
			header = section["section_header"]
			parts = []
			if section["section_name"] == "Operations":
				net = None
				if not no_plan:
					net = _planned_profit(plan, start, before, to, get_fiscal_year, company)
				put("line", (header, "net"), key, net, _("Balance Sheet plan") if no_plan else _("Planned Profit/Loss"))
				parts.append(net)
			for line in section["account_types"]:
				label, account_type = line["label"], line["account_type"]
				actual = (rows["lines"].get((header, label)) or {}).get(key)
				if account_type == "Depreciation":
					amount = _depreciation(dep_segments, start, to, actual)
				else:
					amount = None if no_plan else _balance_effect(leaves_of.get(account_type, []), bal_start, bal_end, actual)
				put("line", (header, label), key, amount, _("Balance Sheet plan") if no_plan and account_type != "Depreciation" else label)
				parts.append(amount)
			adjusted = sum(flt(a.amount) for a in adjustments if a.section == section["section_name"] and start <= getdate(a.posting_date) <= to)
			if any(a.section == section["section_name"] for a in adjustments):
				put("adjustment", header, key, adjusted)
				parts.append(adjusted)
			total = None if any(v is None for v in parts) else sum(parts)
			put("footer", header, key, total)
			if total is None:
				comps = set().union(*(missing.get(("line", k), set()) for k in [(header, "net")] + [(header, l["label"]) for l in section["account_types"]]))
				missing.setdefault(("footer", header), set()).update(comps or {_("Balance Sheet plan")})

		footers = [values[("footer", s["section_header"])][key] for s in sections]
		net_change = None if any(v is None for v in footers) else sum(footers)
		put("net_change", "net", key, net_change)

		opening = None if no_plan else _cash(leaves_of, bal_start, bal_end, "start")
		planned_closing = None if no_plan else _cash(leaves_of, bal_start, bal_end, "end")
		recon["opening"][key] = opening
		recon["net"][key] = net_change
		recon["closing"][key] = None if opening is None or net_change is None else opening + net_change
		recon["planned"][key] = planned_closing
		recon["difference"][key] = (
			None if recon["closing"][key] is None or planned_closing is None else recon["closing"][key] - planned_closing
		)
		if no_plan:
			layer.notes.append(
				_("Plan Incomplete for {0}: no Budgeted Balance Sheet balances (scenario {1}) at {2}.").format(
					p.label, frappe.bold(opts.scenario), formatdate(to if bal_end is None else before)
				)
			)

	_assign(data, rows, layer, values, missing, sections)
	_insert_adjustment_rows(data, rows, layer, values, sections)
	_append_reconciliation(layer, recon, data)

	if opts.show_last_year:
		last_year = ri.run_original(
			original, filters, **_single_range(ri.shift_year(period_list[0].from_date), ri.shift_year(end))
		)
		if last_year is None:
			layer.notes.append(_("Last Year is not available (no fiscal year or data for the previous year)."))
		_fill_actual_by_section(data, layer, last_year)

	out = list(result)
	out[0] = bv.build_columns(columns, layer, opts)
	out[1] = bv.write(data, layer)
	net_values = values.get(("net_change", "net"), {})
	if opts.charts and len(out) > 3:
		out[3] = bv.add_chart_dataset(out[3], _("Budget Net Change in Cash"), [net_values.get(k) for k in layer.keys])
	out = tuple(out) if isinstance(result, tuple) else out
	return ri.with_message(out, "<br>".join(layer.notes))


def _planned_profit(plan, start, before, to, get_fiscal_year, company):
	fy_of_start = get_fiscal_year(start, company=company)
	if fy_of_start[0] != get_fiscal_year(to, company=company)[0]:
		return None  # shortcut: a period spanning two fiscal years is not planned; split the report by year
	ytd_end = plan.profit_loss.get(getdate(to))
	ytd_start = 0.0 if getdate(start) == getdate(fy_of_start[1]) else plan.profit_loss.get(getdate(before))
	return None if ytd_end is None or ytd_start is None else ytd_end - ytd_start


def _depreciation(segments, start, to, actual):
	if not segments:
		return 0.0 if not actual else None
	return sum(allocate(s, start, to) for s in segments.values())


def _balance_effect(leaves, bal_start, bal_end, actual):
	planned = [a for a in leaves if a.name in bal_start or a.name in bal_end]
	if not planned:
		return 0.0 if not actual else None  # nothing planned and nothing happened is a real zero
	if any(a.name not in bal_start or a.name not in bal_end for a in planned):
		return None
	effect = 0.0
	for a in planned:
		change = bal_end[a.name] - bal_start[a.name]
		effect += -change if a.root_type == "Asset" else change
	return effect


def _cash(leaves_of, bal_start, bal_end, side):
	cash = [a.name for t in CASH_TYPES for a in leaves_of.get(t, [])]
	planned = [a for a in cash if a in bal_start or a in bal_end]
	if not planned or any(a not in bal_start or a not in bal_end for a in planned):
		return None
	source = bal_start if side == "start" else bal_end
	return sum(source[a] for a in planned)


def _index_rows(data, sections):
	net_label = "'" + _("Profit for the year") + "'"
	out = {"lines": {}, "line_idx": {}, "footer_idx": {}, "net_change_idx": None}
	footer_of = {"'" + s["section_footer"] + "'": s["section_header"] for s in sections}
	headers = {s["section_header"] for s in sections}
	net_change_label = "'" + _("Net Change in Cash") + "'"
	for idx, row in enumerate(data):
		if not row:
			continue
		section, parent = row.get("section"), row.get("parent_section")
		if parent in headers:
			name = "net" if section == net_label else section
			out["lines"][(parent, name)] = row
			out["line_idx"][(parent, name)] = idx
		elif section in footer_of:
			out["footer_idx"][footer_of[section]] = idx
		elif section == net_change_label and out["net_change_idx"] is None:
			out["net_change_idx"] = idx
	return out


def _status(components):
	return _("Plan Incomplete: {0}").format(", ".join(sorted(components))) if components else None


def _assign(data, rows, layer, values, missing, sections):
	for key, idx in rows["line_idx"].items():
		v = values.get(("line", key))
		layer.set(idx, v, _("Not Planned") if missing.get(("line", key)) else None)
	for header, idx in rows["footer_idx"].items():
		layer.set(idx, values.get(("footer", header)), _status(missing.get(("footer", header))))
	if rows["net_change_idx"] is not None:
		comps = set().union(*(missing.get(("footer", s["section_header"]), set()) for s in sections))
		layer.set(rows["net_change_idx"], values.get(("net_change", "net")), _status(comps))


def _insert_adjustment_rows(data, rows, layer, values, sections):
	for s in sections:
		header = s["section_header"]
		v = values.get(("adjustment", header))
		if v is None or header not in rows["footer_idx"]:
			continue
		label = _("Planned Adjustments")
		layer.inserted.append(
			(
				rows["footer_idx"][header],
				frappe._dict(
					section_name=label,
					section=label,
					parent_section=header,
					indent=1,
					currency=data[rows["footer_idx"][header]].get("currency"),
					fb_budget_only=1,
					fb_status=_("Budget Only (Cash Flow Planning Adjustment)"),
					_fb_budget=v,
				),
			)
		)


def _append_reconciliation(layer, recon, data):
	currency = next((r.get("currency") for r in data if r and r.get("currency")), None)
	header = "'" + _("Planned Cash Reconciliation") + "'"
	layer.inserted.append((None, frappe._dict(section_name=header, section=header, parent_section=None, indent=0, currency=currency, fb_budget_only=1)))
	labels = {
		"opening": _("Planned Opening Cash (Balance Sheet Plan)"),
		"net": _("Planned Net Change in Cash"),
		"closing": _("Planned Closing Cash (Opening + Net Change)"),
		"planned": _("Planned Cash per Balance Sheet Plan"),
		"difference": _("Unreconciled Difference"),
	}
	first, last = layer.keys[0], layer.keys[-1]
	totals = {
		"opening": recon["opening"][first],
		"net": bv.total_of(layer, recon["net"]),
		"closing": recon["closing"][last],
		"planned": recon["planned"][last],
	}
	totals["difference"] = (
		None if totals["closing"] is None or totals["planned"] is None else totals["closing"] - totals["planned"]
	)
	if layer.accumulated:
		totals["net"] = recon["net"][last]
	for name, label in labels.items():
		v = dict(recon[name])
		v["__total__"] = totals[name]
		status = None
		if name == "difference":
			if any(x is None for x in v.values()):
				status = _("Plan Incomplete")
			else:
				status = _("Reconciled") if all(abs(x) < 0.005 for x in v.values()) else _("Unreconciled")
		layer.inserted.append(
			(
				None,
				frappe._dict(
					section_name=label,
					section=label,
					parent_section=header,
					indent=1,
					currency=currency,
					fb_budget_only=1,
					fb_status=status,
					_fb_budget=v,
				),
			)
		)


def _fill_actual_by_section(data, layer, rows):
	for idx, row in enumerate(data):
		if not row or not row.get("section"):
			continue
		other = None if rows is None else rows.get(row["section"])
		layer.extra.setdefault(idx, {})["fb_last_year"] = (
			None if rows is None else (other.get("total") if other else 0.0)
		)
