import copy
import functools
from unittest.mock import patch

import frappe
from erpnext.accounts.report.balance_sheet import balance_sheet as bs
from erpnext.accounts.report.cash_flow import cash_flow as cf
from erpnext.accounts.report.profit_and_loss_statement import profit_and_loss_statement as pl
from frappe.tests.utils import FrappeTestCase

from financial_budget.overrides import query_report as qr_override
from financial_budget.patches import financial_reports as fr
from financial_budget.tests import fixtures as fx
from financial_budget.tests import planning_data as pdata

MODULES = (pl, bs, cf)


def base_function(fn):
	while getattr(fn, "__wrapped__", None):
		fn = fn.__wrapped__
	return fn


class TestMonkeyPatch(fx.BudgetTestCase, FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		pdata.setup_actuals_and_budgets()
		fx.plan(pdata.plan_rows())
		fr.apply()

	def tearDown(self):
		super().tearDown()
		fr.apply()

	def test_installed_on_every_entry_point_and_idempotent(self):
		before = {(m.__name__, a): getattr(m, a) for m in MODULES for a in ("execute", "execute_snapshot_report") if hasattr(m, a)}
		for _ in range(3):
			fr.apply()
		for (module_name, attr), fn in before.items():
			module = frappe.get_module(module_name)
			self.assertIs(getattr(module, attr), fn)
			self.assertTrue(fr.is_wrapped(fn))
			self.assertEqual(base_function(fn).__module__, module_name)  # one wrapper over ERPNext's function
			self.assertIs(fn.__wrapped__, base_function(fn))
		self.assertEqual(pl.execute.__name__, "execute")  # functools.wraps

	def test_remove_and_reapply_from_hooks(self):
		fr.remove()
		self.assertFalse(fr.is_wrapped(pl.execute))
		for hook in ("before_request", "before_job"):
			self.assertIn("financial_budget.patches.financial_reports.apply", frappe.get_hooks(hook))
		frappe.call(frappe.get_hooks("before_job")[-1], method="x", kwargs={}, transaction_type="job")
		self.assertTrue(all(fr.is_wrapped(m.execute) for m in MODULES))

	def test_disabled_returns_exact_original(self):
		cases = [
			({"enabled": 0}, {}),
			({"enable_profit_and_loss": 0, "enable_balance_sheet": 0, "enable_cash_flow": 0}, {}),
			({}, {"show_budget": 0}),
		]
		for settings, filters in cases:
			fx.set_settings(**{"enabled": 1, "enable_profit_and_loss": 1, "enable_balance_sheet": 1, "enable_cash_flow": 1, **settings})
			for module in MODULES:
				f = fx.filters(periodicity="Yearly", accumulated_values=1 if module is bs else 0, **filters)
				original = base_function(module.execute)(copy.deepcopy(f))
				wrapped = module.execute(copy.deepcopy(f))
				self.assertEqual(wrapped, original, f"{module.__name__} {settings} {filters}")
		fx.set_settings(enabled=1, enable_profit_and_loss=1, enable_balance_sheet=1, enable_cash_flow=1)

	def test_site_without_the_app_gets_the_original_object(self):
		sentinel = ([], [], None)
		original = lambda filters: sentinel  # noqa: E731
		wrapper = fr._make_wrapper("Profit and Loss Statement", original)
		apps = [a for a in frappe.get_installed_apps() if a != "financial_budget"]
		with patch("frappe.get_installed_apps", return_value=apps):
			self.assertIs(wrapper(fx.filters()), sentinel)

	def test_budget_failure_returns_original(self):
		def boom(*a, **k):
			raise RuntimeError("budget layer bug")

		f = fx.filters(periodicity="Yearly")
		original = base_function(pl.execute)(copy.deepcopy(f))
		with patch("financial_budget.financial_budget.profit_and_loss.build", boom):
			result = pl.execute(copy.deepcopy(f))
		self.assertEqual(result[0], original[0])
		self.assertEqual(result[1], original[1])
		self.assertIn("could not be calculated", result[2])

	def test_foreign_patch_is_preserved_and_reported(self):
		fr.remove()
		erpnext_execute = pl.execute
		calls = []

		@functools.wraps(erpnext_execute)
		def foreign(filters=None):
			calls.append(1)
			return erpnext_execute(filters)

		foreign.__module__ = "some_other_app.patches"
		pl.execute = foreign
		try:
			fr.apply()
			self.assertTrue(fr.is_wrapped(pl.execute))
			self.assertIs(pl.execute.__wrapped__, foreign)
			self.assertIn("foreign", fr.STATUS[pl.__name__ + ".execute"])
			pl.execute(fx.filters(periodicity="Yearly"))
			self.assertEqual(calls, [1])
			fr.apply()
			self.assertIs(pl.execute.__wrapped__, foreign)  # still wrapped once
		finally:
			pl.execute = erpnext_execute
			fr.STATUS.clear()

	def test_incompatible_or_missing_api_is_not_patched(self):
		fr.remove()
		erpnext_execute, snapshot = pl.execute, pl.execute_snapshot_report
		try:
			pl.execute = lambda: None  # noqa: E731
			del pl.execute_snapshot_report
			fr.apply()
			self.assertFalse(fr.is_wrapped(pl.execute))
			self.assertIn("incompatible", fr.STATUS[pl.__name__ + ".execute"])
			self.assertIn("missing", fr.STATUS[pl.__name__ + ".execute_snapshot_report"])
		finally:
			pl.execute, pl.execute_snapshot_report = erpnext_execute, snapshot
			fr.STATUS.clear()

	def test_permission_hides_budget(self):
		user = "fb.accounts.user@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(doctype="User", email=user, first_name="FB", send_welcome_email=0, roles=[{"role": "Accounts User"}]).insert()
		frappe.set_user(user)
		try:
			f = fx.filters(periodicity="Yearly")
			result = pl.execute(copy.deepcopy(f))
			original = base_function(pl.execute)(copy.deepcopy(f))
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(result[1], original[1])
		self.assertIn("read permission on Budget", result[2])

	def test_get_script_override_is_scoped(self):
		from frappe.desk import query_report

		self.assertEqual(frappe.override_whitelisted_method(qr_override.METHOD), qr_override.OURS)
		self.assertEqual(qr_override.conflicts(), [])
		for name in qr_override.TARGETS:
			original = query_report.get_script(name)
			ours = qr_override.get_script(name)
			self.assertTrue(ours["script"].startswith(original["script"]))
			self.assertIn(f"financial_budget.setup_report({frappe.as_json(name)}", ours["script"])
			self.assertIn("existing.fieldname === f.fieldname", ours["script"])  # no duplicate filters
		self.assertEqual(qr_override.get_script("General Ledger"), query_report.get_script("General Ledger"))
		fx.set_settings(enabled=0)
		self.assertEqual(qr_override.get_script("Balance Sheet"), query_report.get_script("Balance Sheet"))
		fx.set_settings(enabled=1)

	def test_get_script_keeps_permission_check(self):
		user = "fb.no.access@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(doctype="User", email=user, first_name="FB", send_welcome_email=0).insert()
		frappe.set_user(user)
		try:
			self.assertRaises(frappe.PermissionError, qr_override.get_script, "Balance Sheet")
		finally:
			frappe.set_user("Administrator")
