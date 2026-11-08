import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from financial_budget.financial_budget.cash_flow import CASH_TYPES, DERIVED_TYPES


class CashFlowPlanningAdjustment(Document):
	def validate(self):
		self.currency = frappe.get_cached_value("Company", self.company, "default_currency")
		if not flt(self.amount):
			frappe.throw(_("Cash Effect cannot be zero"))
		if self.account:
			acc = frappe.get_cached_value("Account", self.account, ["company", "account_type"], as_dict=True)
			if acc.company != self.company:
				frappe.throw(_("Account {0} does not belong to company {1}").format(self.account, self.company))
			if acc.account_type in (*DERIVED_TYPES, *CASH_TYPES):
				frappe.throw(
					_(
						"{0} is a {1} account: its movement is already in the Cash Flow plan from the Budgeted Balance Sheet. An adjustment would count it twice."
					).format(self.account, _(acc.account_type))
				)
