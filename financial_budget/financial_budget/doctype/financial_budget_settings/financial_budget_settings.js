frappe.ui.form.on("Financial Budget Settings", {
	refresh(frm) {
		frappe.call("financial_budget.financial_budget.doctype.financial_budget_settings.financial_budget_settings.get_integration_status").then((r) => {
			const s = r.message || {};
			const rows = Object.entries(s.patches || {})
				.map(([k, v]) => `<tr><td><code>${frappe.utils.escape_html(k)}</code></td><td>${frappe.utils.escape_html(v)}</td></tr>`)
				.join("");
			const conflicts = (s.get_script_conflicts || []).length
				? `<p class="text-danger">${__("These apps override get_script after this app, so the budget filters will not appear:")} ${frappe.utils.escape_html(s.get_script_conflicts.join(", "))}</p>`
				: `<p class="text-muted">${__("No get_script override conflicts.")}</p>`;
			frm.get_field("integration_status").$wrapper.html(
				`<table class="table table-bordered small"><tbody>${rows}</tbody></table>${conflicts}`
			);
		});
	},
});
