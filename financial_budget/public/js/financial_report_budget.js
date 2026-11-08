// Budget filters for the standard Profit and Loss Statement, Balance Sheet and Cash Flow.
// Appended to the report's own script by financial_budget.overrides.query_report.get_script.
// Only adds filters (once, by fieldname); formatter, tree and drill-down stay ERPNext's.
frappe.provide("financial_budget");

financial_budget.setup_report = function (report_name, config) {
	const report = frappe.query_reports[report_name];
	if (!report || !Array.isArray(report.filters)) return;

	const shown = "eval:doc.show_budget";
	const filters = [
		{ fieldname: "show_budget", label: __("Show Budget"), fieldtype: "Check", default: 0 },
		report_name === "Profit and Loss Statement"
			? {
					fieldname: "budget_source",
					label: __("Budget Source"),
					fieldtype: "Select",
					options: [
						{ value: "Cost Center", label: __("Cost Center Budgets") },
						{ value: "Project", label: __("Project Budgets") },
					],
					default: "Cost Center",
					depends_on: shown,
			  }
			: {
					fieldname: "budget_scenario",
					label: __("Budget Scenario"),
					fieldtype: "Data",
					default: "Base",
					depends_on: shown,
			  },
		{
			fieldname: "budget_comparison",
			label: __("Budget Comparison"),
			fieldtype: "Select",
			options: [
				{ value: "Per Period", label: __("Per Period") },
				{ value: "Total Only", label: __("Total Only") },
			],
			default: config.comparison || "Per Period",
			depends_on: shown,
		},
		{ fieldname: "show_variance", label: __("Show Variance"), fieldtype: "Check", default: 1, depends_on: shown },
		{
			fieldname: "show_variance_pct",
			label: __("Show Variance %"),
			fieldtype: "Check",
			default: config.show_variance_pct ? 1 : 0,
			depends_on: shown,
		},
		{ fieldname: "show_last_year", label: __("Show Last Year"), fieldtype: "Check", default: 0, depends_on: shown },
	];
	if (report_name === "Profit and Loss Statement") {
		filters.push({
			fieldname: "show_ytd",
			label: __("YTD Comparison"),
			fieldtype: "Check",
			default: 0,
			depends_on: shown,
		});
	}

	for (const f of filters) {
		if (!report.filters.some((existing) => existing.fieldname === f.fieldname)) {
			report.filters.push(f);
		}
	}
};
