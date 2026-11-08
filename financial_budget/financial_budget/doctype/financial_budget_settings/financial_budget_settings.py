import frappe
from frappe.model.document import Document


class FinancialBudgetSettings(Document):
	pass


@frappe.whitelist()
def get_integration_status():
	"""Patch state of this worker process and get_script override conflicts."""
	frappe.only_for(("System Manager", "Accounts Manager"))
	from financial_budget.overrides.query_report import conflicts
	from financial_budget.patches import financial_reports

	financial_reports.apply()
	return {"patches": financial_reports.STATUS, "get_script_conflicts": conflicts()}
