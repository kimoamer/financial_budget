"""Turn annual budgets into dated segments and allocate them to report periods."""

from frappe.utils import add_days, add_months, date_diff, getdate


def fiscal_year_months(year_start, year_end):
	"""Month segments of a fiscal year, anchored on its start date (any start day, any length)."""
	year_start, year_end = getdate(year_start), getdate(year_end)
	segments, cur, i = [], year_start, 0
	while cur <= year_end:
		seg_end = min(add_days(add_months(year_start, i + 1), -1), year_end)
		segments.append((cur, seg_end))
		cur, i = add_days(seg_end, 1), i + 1
	return segments


def budget_segments(amount, year_start, year_end, distribution=None):
	"""Split an annual amount over the fiscal-year months.

	distribution: {"January": 10.0, ...} percentages (ERPNext Monthly Distribution), or None
	for an equal split. A month name that occurs in several segments (fiscal year longer
	than 12 months) shares its percentage equally, so the total never exceeds the budget.
	"""
	months = fiscal_year_months(year_start, year_end)
	if not distribution:
		return [(s, e, amount / len(months)) for s, e in months]

	name_count = {}
	for s, _e in months:
		name_count[s.strftime("%B")] = name_count.get(s.strftime("%B"), 0) + 1
	return [
		(s, e, amount * (distribution.get(s.strftime("%B"), 0.0) / 100.0) / name_count[s.strftime("%B")])
		for s, e in months
	]


def allocate(segments, from_date, to_date):
	"""Amount of the segments that falls in [from_date, to_date], pro-rated by days."""
	from_date, to_date = getdate(from_date), getdate(to_date)
	total = 0.0
	for s, e, amount in segments:
		lo, hi = max(s, from_date), min(e, to_date)
		if lo <= hi:
			total += amount * (date_diff(hi, lo) + 1) / (date_diff(e, s) + 1)
	return total
