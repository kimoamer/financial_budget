"""Scoped override of frappe.desk.query_report.get_script.

Delegates to the previous implementation (another app's override or Frappe's own, so
permission checks stay intact) and appends the budget filter script for the three
standard financial reports only. Every other report is returned untouched.
"""

import json
import os

import frappe

METHOD = "frappe.desk.query_report.get_script"
OURS = "financial_budget.overrides.query_report.get_script"
TARGETS = ("Profit and Loss Statement", "Balance Sheet", "Cash Flow")
_JS_PATH = os.path.join(os.path.dirname(__file__), "..", "public", "js", "financial_report_budget.js")


def previous_method():
	"""The override registered before ours, else Frappe's original."""
	chain = frappe.get_hooks("override_whitelisted_methods", {}).get(METHOD, [])
	if OURS in chain:
		before = chain[: chain.index(OURS)]
		if before:
			return frappe.get_attr(before[-1])
	from frappe.desk import query_report

	return query_report.get_script


def conflicts():
	"""Overrides registered after ours win and hide the budget filters."""
	chain = frappe.get_hooks("override_whitelisted_methods", {}).get(METHOD, [])
	return chain[chain.index(OURS) + 1 :] if OURS in chain else []


@frappe.whitelist()
def get_script(report_name: str):
	result = previous_method()(report_name)
	if report_name not in TARGETS or not isinstance(result, dict) or not result.get("script"):
		return result

	from financial_budget.financial_budget.report_integration import get_settings, is_enabled

	if not is_enabled(report_name):
		return result

	settings = get_settings()
	config = {
		"comparison": settings.default_comparison_mode or "Per Period",
		"show_variance_pct": int(settings.show_variance_pct_by_default or 0),
	}
	with open(_JS_PATH) as f:
		js = f.read()
	result = dict(result)
	result["script"] += (
		f"\n\n{js}\nfinancial_budget.setup_report({json.dumps(report_name)}, {json.dumps(config)});"
		"\n//# sourceURL=financial_report_budget.js"
	)
	return result
