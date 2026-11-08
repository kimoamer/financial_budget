frappe.ui.form.on("Budgeted Balance Sheet", {
	setup(frm) {
		frm.set_query("account", "accounts", () => ({
			filters: { company: frm.doc.company, is_group: 0, root_type: ["in", ["Asset", "Liability", "Equity"]] },
		}));
	},
	refresh(frm) {
		frm.fields_dict.periods.grid.update_docfield_property(
			"planned_profit_loss",
			"read_only",
			frm.doc.profit_loss_source !== "Manual"
		);
	},
	profit_loss_source(frm) {
		frm.trigger("refresh");
	},
});

frappe.ui.form.on("Budgeted Balance Sheet Account", {
	opening_planned_balance: (frm, cdt, cdn) => set_closing(cdt, cdn),
	planned_movement: (frm, cdt, cdn) => set_closing(cdt, cdn),
});

function set_closing(cdt, cdn) {
	const row = locals[cdt][cdn];
	frappe.model.set_value(cdt, cdn, "planned_closing_balance", flt(row.opening_planned_balance) + flt(row.planned_movement));
}
