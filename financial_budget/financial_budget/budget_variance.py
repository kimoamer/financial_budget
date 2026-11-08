"""Budget columns, variance arithmetic and writing the budget layer onto report rows.

Only new `fb_*` keys are written to existing rows; original keys are never touched.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt


class Layer:
	"""Budget values computed for one report run, keyed by row index in the original data."""

	def __init__(self, period_list, accumulated):
		self.period_list = period_list
		self.keys = [p.key for p in period_list]
		self.accumulated = bool(cint(accumulated))
		self.budget = {}  # idx -> {period_key: amount | None}
		self.status = {}  # idx -> text
		self.extra = {}  # idx -> {"fb_last_year": .., "fb_ytd_budget": .., "fb_ytd_actual": ..}
		self.inserted = []  # (insert_before_idx | None for end, row)
		self.cost_rows = set()  # idx of expense rows: variance = budget - actual, so favourable is positive
		self.notes = []

	def set(self, idx, values, status=None):
		self.budget[idx] = values
		if status:
			self.status[idx] = status


def precision():
	return cint(frappe.db.get_default("currency_precision")) or 2


def variance(actual, budget, cost=False):
	"""Favourable variance is positive: actual - budget, or budget - actual for an expense (cost=True)."""
	if actual is None or budget is None:
		return None, None
	diff = flt(budget) - flt(actual) if cost else flt(actual) - flt(budget)
	pct = diff / abs(flt(budget)) * 100 if flt(budget) else None
	return diff, pct


def total_of(layer, values):
	"""Budget for the 'total' comparison: last period when accumulated, else the sum of periods.
	None if any period is missing (a partial sum would look like a complete budget)."""
	if values is None:
		return None
	if layer.accumulated:
		return values.get(layer.keys[-1])
	period_values = [values.get(k) for k in layer.keys]
	return None if any(v is None for v in period_values) else sum(period_values)


def actual_total(layer, row):
	return row.get(layer.keys[-1]) if layer.accumulated else row.get("total")


def build_columns(columns, layer, opts):
	"""Per compared amount: Budget, then the actual column (labelled "... Actual"), then the variances."""
	per_period = opts.comparison == "Per Period"
	with_total = not per_period or len(layer.keys) > 1
	new_columns, total_block_added = [], False

	def variances(suffix, label):
		cols = []
		if opts.show_variance:
			cols.append(_currency(f"fb_variance_{suffix}", label + " " + _("Variance")))
		if opts.show_variance_pct:
			cols.append(
				{"fieldname": f"fb_variance_pct_{suffix}", "label": label + " " + _("Variance %"), "fieldtype": "Percent", "width": 110}
			)
		return cols

	def budget(suffix, label):
		return _currency(f"fb_budget_{suffix}", label + " " + _("Budget"))

	def actual(col):
		return {**col, "label": (col.get("label") or "") + " " + _("Actual")}  # a copy: ERPNext's column dict stays as is

	labels = {p.key: p.label for p in layer.period_list}
	total_label = _("Total")
	for col in columns:
		fieldname = col.get("fieldname") if isinstance(col, dict) else None
		if fieldname in labels:
			if per_period:
				new_columns += [budget(fieldname, labels[fieldname]), actual(col), *variances(fieldname, labels[fieldname])]
			else:
				new_columns.append(actual(col))
		elif fieldname == "total" and with_total and not total_block_added:
			new_columns += [budget("total", total_label), actual(col), *variances("total", total_label)]
			total_block_added = True
		else:
			new_columns.append(col)

	if with_total and not total_block_added:  # no ERPNext total column (balances, single period): compare at the end
		new_columns += [budget("total", total_label), *variances("total", total_label)]
	if opts.show_last_year:
		new_columns.append(_currency("fb_last_year", _("Last Year")))
	if opts.show_ytd:
		new_columns.append(_currency("fb_ytd_budget", _("YTD Budget")))
		new_columns.append(_currency("fb_ytd_actual", _("YTD Actual")))
	new_columns.append({"fieldname": "fb_status", "label": _("Budget Status"), "fieldtype": "Data", "width": 160})
	return new_columns


def write(data, layer):
	"""Write fb_* values onto rows and insert budget-only rows. Returns the data list."""
	p = precision()
	for idx, row in enumerate(data):
		if not row:
			continue
		_write_row(row, layer.budget.get(idx), layer, p, idx in layer.cost_rows)
		if idx in layer.status:
			row["fb_status"] = layer.status[idx]
		row.update(layer.extra.get(idx, {}))

	for _before, row in layer.inserted:
		_write_row(row, row.pop("_fb_budget", None), layer, p, row.pop("_fb_cost", False))

	# insert from the back so earlier indexes stay valid; same index keeps insertion order
	ordered = sorted(
		((before, seq, row) for seq, (before, row) in enumerate(layer.inserted) if before is not None),
		key=lambda x: (x[0], x[1]),
		reverse=True,
	)
	for before, _seq, row in ordered:
		data.insert(before, row)
	data.extend(row for before, row in layer.inserted if before is None)
	return data


def _write_row(row, values, layer, p, cost=False):
	values = values or {}  # missing budget is written as None, never 0
	for key in layer.keys:
		budget = values.get(key)
		_put(row, key, budget, row.get(key), p, cost)
	# "__total__" lets a row define its own total (e.g. an opening balance is not summed)
	total = values["__total__"] if "__total__" in values else total_of(layer, values)
	_put(row, "total", total, actual_total(layer, row), p, cost)


def _put(row, suffix, budget, actual, p, cost=False):
	row[f"fb_budget_{suffix}"] = None if budget is None else flt(budget, p)
	diff, pct = variance(actual, None if budget is None else flt(budget, p), cost)
	row[f"fb_variance_{suffix}"] = None if diff is None else flt(diff, p)
	row[f"fb_variance_pct_{suffix}"] = None if pct is None else flt(pct, 2)


def _currency(fieldname, label):
	return {"fieldname": fieldname, "label": label, "fieldtype": "Currency", "options": "currency", "width": 140}


def rollup(accounts, leaf_values, keys):
	"""Sum leaf {account: {key: amount}} into every ancestor. Accounts without any budgeted
	descendant stay absent (missing, not zero)."""
	totals = {a: dict(v) for a, v in leaf_values.items()}
	for name in sorted(accounts, key=lambda n: -accounts[n].lft):
		parent = accounts[name].parent_account
		if name in totals and parent in accounts:
			dest = totals.setdefault(parent, dict.fromkeys(keys, 0.0))
			for k in keys:
				dest[k] = dest.get(k, 0.0) + totals[name].get(k, 0.0)
	return totals


def add_chart_dataset(chart, name, values):
	"""Return a copy of chart with one extra dataset (the original chart dict is not modified)."""
	if not chart or not chart.get("data") or any(v is None for v in values):
		return chart
	new_chart = dict(chart)
	new_chart["data"] = dict(chart["data"])
	new_chart["data"]["datasets"] = [*chart["data"].get("datasets", []), {"name": name, "values": values}]
	return new_chart
