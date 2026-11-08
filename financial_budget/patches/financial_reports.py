"""Safe, idempotent wrappers around the standard ERPNext financial report entry points.

Not a migration patch (it is not listed in patches.txt). See docs/architecture.md.

Rules enforced here:
- the original function always runs first and its result is the base of the output;
- if the integration is not active for the *current site and call*, the wrapper
  returns exactly what the original returned (same object);
- any error in the budget layer logs and returns the original result unchanged;
- a function is never wrapped twice, and foreign wrappers are preserved (we wrap them).
"""

import copy
import functools
import importlib
import inspect

import frappe

MARK = "__financial_budget_wrapper__"

# report name -> (module path, entry points that exist in ERPNext 15.121.6 / 15.124.1)
TARGETS = {
	"Profit and Loss Statement": (
		"erpnext.accounts.report.profit_and_loss_statement.profit_and_loss_statement",
		("execute", "execute_snapshot_report"),
	),
	"Balance Sheet": (
		"erpnext.accounts.report.balance_sheet.balance_sheet",
		("execute", "execute_snapshot_report"),
	),
	"Cash Flow": ("erpnext.accounts.report.cash_flow.cash_flow", ("execute",)),
}

# entry point -> reason it was not wrapped / foreign wrapper found (for the status page)
STATUS = {}


def apply():
	"""Install the wrappers. Cheap and idempotent: safe to call on every request/job."""
	for report_name, (module_path, attrs) in TARGETS.items():
		try:
			module = importlib.import_module(module_path)
		except Exception as e:
			STATUS[module_path] = f"not patched: import failed ({e})"
			continue
		for attr in attrs:
			key = f"{module_path}.{attr}"
			current = getattr(module, attr, None)
			if current is None:
				STATUS[key] = "not patched: entry point missing in this ERPNext version"
				continue
			if is_wrapped(current):
				continue
			if not _compatible(current):
				STATUS[key] = "not patched: incompatible signature"
				continue
			if getattr(current, "__wrapped__", None) or getattr(current, "__module__", None) != module_path:
				STATUS[key] = f"wrapped a foreign patch: {getattr(current, '__module__', '?')}"
			else:
				STATUS[key] = "patched"
			setattr(module, attr, _make_wrapper(report_name, current))


def remove():
	"""Restore the functions we replaced. A wrapper buried under a later foreign patch is
	left in place (unwrapping it would drop the foreign patch); it is inert once the app
	is uninstalled or disabled."""
	for module_path, attrs in TARGETS.values():
		module = importlib.import_module(module_path)
		for attr in attrs:
			current = getattr(module, attr, None)
			if current is not None and getattr(current, MARK, None):
				setattr(module, attr, current.__wrapped__)


def before_uninstall():
	remove()


def is_wrapped(fn):
	"""True if our wrapper is anywhere in the __wrapped__ chain."""
	seen = 0
	while fn is not None and seen < 20:
		if getattr(fn, MARK, None):
			return True
		fn = getattr(fn, "__wrapped__", None)
		seen += 1
	return False


def _compatible(fn):
	try:
		params = list(inspect.signature(fn).parameters.values())
	except (TypeError, ValueError):
		return False
	return bool(params) and params[0].kind in (params[0].POSITIONAL_ONLY, params[0].POSITIONAL_OR_KEYWORD)


def _make_wrapper(report_name, original):
	@functools.wraps(original)
	def wrapper(filters=None, *args, **kwargs):
		from financial_budget.financial_budget import report_integration

		try:
			active = report_integration.is_active(report_name, filters)
		except Exception:
			frappe.log_error(title="Financial Budget: activation check failed")
			active = False

		if not active:
			return original(filters, *args, **kwargs)

		# the standard reports mutate their filters (e.g. Cash Flow sets start_date)
		snapshot = copy.deepcopy(filters)
		result = original(filters, *args, **kwargs)
		return report_integration.enhance(report_name, snapshot, result, original)

	setattr(wrapper, MARK, report_name)
	return wrapper
