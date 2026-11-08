import frappe


def after_install():
	# persist the Single's defaults so every worker reads the same explicit values
	frappe.get_single("Financial Budget Settings").save(ignore_permissions=True)
