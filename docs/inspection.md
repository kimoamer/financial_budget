# Inspection report (2026-11-08)

## Versions

- Bench: Frappe v15, ERPNext **15.121.6** installed. The spec targets 15.124.1: the patched files
  `profit_and_loss_statement.py`, `balance_sheet.py`, `cash_flow.py` and `financial_statements.py` are
  byte-identical between the installed version and the `v15.124.1` tag. Between the two versions,
  only `public/js/financial_statements.js` (link-field options) and `budget.py` (query builder refactor
  of the ordered-amount query) differ; neither is used by this app.
- `erpnext_cash_management` is not imported or modified.

## Integration points used

| Point | Why it is safe |
|---|---|
| `Report.execute_module` → `frappe.get_attr("<module>.execute")` | resolved on every call, so replacing the module attribute reaches UI, export, print, prepared reports and jobs |
| `execute_snapshot_report` (P&L, BS) | used when a report is a snapshot report; wrapped the same way |
| `hooks.before_request` (`frappe/app.py`) / `before_job` (`background_jobs.execute_job`) | activation in web and worker processes |
| `override_whitelisted_methods["frappe.desk.query_report.get_script"]` | adds filters to the three reports only; chains to any earlier override |
| ERPNext `financial_statements.get_period_list`, `get_accounts`, `filter_accounts`, `get_cost_centers_with_children`, Cash Flow `get_cash_flow_accounts`, `get_start_date` | read-only helpers, same signatures in both versions |

## Report data contracts relied on

- P&L / BS rows: `account`, `parent_account`, `indent`, period keys (`jun_2027` …), `total`, `currency`;
  total rows labelled `'Total {root} ({balance})'`, `'Profit for the year'`,
  `'Provisional Profit / Loss (Credit)'`, `'Total (Credit)'`.
- Cash Flow rows: `section`, `parent_section`, footers quoted like `'Net Cash from Operations'`.
- The Cash Flow report mutates its `filters`; the wrapper deep-copies filters before calling it.
- ERPNext's print template (`financial_statements.html`) refuses more than 8 visible columns.

## End-to-end verification on staging.local (test site, fresh company `FB E2E Co`)

| Path | Result |
|---|---|
| `frappe.desk.query_report.run` via token-auth HTTP (P&L, BS, CF) | `fb_*` columns present; `show_budget=0` → no `fb_*` columns |
| `get_script` (3 reports) | contains `financial_budget.setup_report`; General Ledger unchanged |
| `export_query` CSV / Excel (BS) | budget columns exported (`2026-2027 Budget`, `Variance`, `Variance %`, `Last Year`, `Budget Status`) |
| Prepared Report (BS) processed by an RQ worker | Completed; FB Cash actual 1750, budget 10100, variance −8350 (`before_job` path) |
| Browser (Chromium, desk UI) | budget filters shown once, columns/indent/summary cards/chart rendered, no JS errors |
| Print (BS yearly) | printed with budget columns |
| Print (P&L quarterly) | refused by ERPNext's 8-column limit — see limitations |
| Cross-site: wrappers installed in a process, report run on `dist2.local` (app not installed) | P&L and BS results identical to the originals |

## Performance (staging, 400 accounts, 303 budget lines, 3 609 GL entries, median of 5)

| Report | Original | With budget | Overhead |
|---|---|---|---|
| P&L monthly | 233 ms | 309 ms | +77 ms |
| P&L monthly + Last Year + YTD | 238 ms | 547 ms | +309 ms (two extra runs of the original report) |
| Balance Sheet monthly, accumulated | 252 ms | 260 ms | +8 ms |
| Cash Flow monthly | 343 ms | 367 ms | +25 ms |

With `show_budget` off or the integration disabled the overhead is one settings lookup (cached doc).

## Known limitations

- Print: ERPNext's template allows max 8 visible columns. Monthly/quarterly reports with budget
  columns exceed it (as plain monthly reports already do); print yearly, or export to Excel.
- Balance Sheet / Cash Flow plans are company-level; cost-center/project/dimension filters hide the
  budget columns.
- Balance Sheet needs Accumulated Values; Growth/Margin views are not enhanced.
- Cash Flow: a period that crosses a fiscal-year boundary gets an empty profit line (`Plan Incomplete`).
- Presentation currency other than the company currency converts budgets at the period-end rate; when
  no rate is available the budget columns are empty. ERPNext may try to fetch the rate from the
  internet (Currency Exchange Settings).
- Accounts with a budget but no actual are hidden unless *Display Zero-Budget Accounts* is enabled in
  the settings (they are always included in the group totals).
- `bench execute` / `bench console` / scripts need `apply()` first (no request/job hook there).
