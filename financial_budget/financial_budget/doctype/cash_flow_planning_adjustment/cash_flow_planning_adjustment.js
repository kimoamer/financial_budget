frappe.ui.form.on("Cash Flow Planning Adjustment", {
	setup(frm) {
		frm.set_query("account", () => ({ filters: { company: frm.doc.company, is_group: 0 } }));
	},
});
