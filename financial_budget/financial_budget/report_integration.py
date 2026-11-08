"""Activation checks and the entry point the report wrappers call."""

import copy

import frappe
from frappe import _
from frappe.utils import add_months, cint, getdate

APP = "financial_budget"
REPORT_FLAG = {
	"Profit and Loss Statement": "enable_profit_and_loss",
	"Balance Sheet": "enable_balance_sheet",
	"Cash Flow": "enable_cash_flow",
}
# doctypes the user must be able to read to see the budget layer of a report
REQUIRED_READ = {
	"Profit and Loss Statement": ("Budget",),
	"Balance Sheet": ("Budgeted Balance Sheet",),
	"Cash Flow": ("Budgeted Balance Sheet", "Budget", "Cash Flow Planning Adjustment"),
}


def get_settings():
	return frappe.get_cached_doc("Financial Budget Settings")


def is_enabled(report_name):
	"""App installed on *this* site and the report enabled in settings."""
	if not getattr(frappe.local, "site", None) or APP not in frappe.get_installed_apps():
		return False
	settings = get_settings()
	return bool(cint(settings.enabled) and cint(settings.get(REPORT_FLAG[report_name])))


def is_active(report_name, filters):
	# cheapest check first: nothing to do unless the user asked for the budget
	if not filters or not cint(filters.get("show_budget")):
		return False
	return is_enabled(report_name)


def options(report_name, filters):
	settings = get_settings()
	o = frappe._dict(
		comparison=filters.get("budget_comparison") or settings.default_comparison_mode or "Per Period",
		show_variance=cint(filters.get("show_variance", 1)),
		show_variance_pct=cint(filters.get("show_variance_pct", settings.show_variance_pct_by_default)),
		show_last_year=cint(filters.get("show_last_year")),
		show_ytd=cint(filters.get("show_ytd")) and report_name == "Profit and Loss Statement",
		charts=cint(settings.enable_budget_charts),
		zero_budget_accounts=cint(settings.display_zero_budget_accounts),
		scenario=filters.get("budget_scenario") or "Base",
		budget_source=filters.get("budget_source") or "Cost Center",
	)
	if o.budget_source not in ("Cost Center", "Project"):
		frappe.throw(_("Budget Source must be Cost Center or Project"))
	return o


def enhance(report_name, filters, result, original):
	"""Add the budget layer to the original result. On any failure the original result
	is returned untouched (the budget layer is computed before anything is written)."""
	from financial_budget.financial_budget import balance_sheet, cash_flow, profit_and_loss

	builder = {
		"Profit and Loss Statement": profit_and_loss.build,
		"Balance Sheet": balance_sheet.build,
		"Cash Flow": cash_flow.build,
	}[report_name]

	missing = [dt for dt in REQUIRED_READ[report_name] if not frappe.has_permission(dt, "read")]
	if missing:
		return with_message(
			result,
			_("Budget columns are hidden: you need read permission on {0}.").format(", ".join(_(d) for d in missing)),
		)

	opts = options(report_name, filters)  # invalid user input is reported, not swallowed
	log_len = len(frappe.local.message_log or [])
	try:
		return builder(filters, result, original, opts)
	except Exception:
		drop_messages(log_len)
		frappe.log_error(title=f"Financial Budget: {report_name} failed")
		return with_message(result, _("Budget columns could not be calculated; see Error Log."))


def with_message(result, note):
	"""Same tuple with `note` appended to the message slot (index 2)."""
	if not note or not isinstance(result, tuple | list) or len(result) < 3:
		return result
	out = list(result)
	out[2] = f"{out[2]}<br>{note}" if out[2] else note
	return type(result)(out)


def blocked_by_filters(filters, allowed=()):
	"""Name of the first active GL filter a company-level plan cannot honour."""
	from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions

	for fieldname in ("cost_center", "project", *get_accounting_dimensions()):
		if fieldname not in allowed and filters.get(fieldname):
			return fieldname
	return None


def run_original(original, filters, **overrides):
	"""Run the untouched report with changed filters; {row key: row} or None if it fails.
	Messages the original raises are removed so they do not reach the user."""
	f = copy.deepcopy(filters)
	f.update(overrides)
	f.show_budget = 0
	log_len = len(frappe.local.message_log or [])
	try:
		result = original(f)
	except Exception:
		drop_messages(log_len)
		return None
	rows = {}
	for row in result[1] or []:
		key = row.get("account") or row.get("section")
		if key:
			rows[key] = row
	return rows


def drop_messages(keep):
	if frappe.local.message_log:
		del frappe.local.message_log[keep:]


def period_list_for(filters):
	"""The same period list the standard reports build from these filters."""
	from erpnext.accounts.report.financial_statements import get_period_list

	return get_period_list(
		filters.from_fiscal_year,
		filters.to_fiscal_year,
		filters.period_start_date,
		filters.period_end_date,
		filters.filter_based_on,
		filters.periodicity,
		company=filters.company,
	)


def shift_year(date, years=-1):
	return add_months(getdate(date), 12 * years)


def conversion_rate(filters, company_currency, report_date):
	"""Rate to show company-currency budgets in the presentation currency (None if unknown)."""
	target = filters.get("presentation_currency")
	if not target or target == company_currency:
		return 1.0
	from erpnext.setup.utils import get_exchange_rate

	return get_exchange_rate(company_currency, target, report_date) or None
