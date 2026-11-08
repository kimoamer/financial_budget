# Architecture

`financial_budget` adds budget / plan columns to three **standard** ERPNext reports without
copying them and without editing `erpnext` or `frappe`:

| Report | Budget source | Comparison |
|---|---|---|
| Profit and Loss Statement | submitted ERPNext **Budget** (+ Monthly Distribution) | actual vs budget per period / total, Last Year, YTD |
| Balance Sheet | submitted **Budgeted Balance Sheet** (scenario) | actual balance vs planned closing balance |
| Cash Flow | derived from the Budgeted Balance Sheet + Budgets + **Cash Flow Planning Adjustment** | indirect method, reconciled to planned cash |

## Request flow

```
query_report.run / export_query / Prepared Report job / snapshot
  └─ Report.execute_module  → frappe.get_attr("<erpnext report module>.execute")   (resolved at call time)
       └─ financial_budget wrapper  (patches/financial_reports.py)
            ├─ is_active(report, filters)?  app installed on THIS site, settings enabled, show_budget=1
            │     no  → return original(filters)            (same object, untouched)
            ├─ snapshot = deepcopy(filters); result = original(filters)
            └─ report_integration.enhance(report, snapshot, result, original)
                 ├─ permission check (read on Budget / Budgeted Balance Sheet / Cash Flow Planning Adjustment)
                 ├─ profit_and_loss.build | balance_sheet.build | cash_flow.build
                 └─ any exception → Error Log + original result + "could not be calculated" message
```

The wrapper never changes an existing row value or column; it only appends `fb_*` keys to rows,
`fb_*` columns, report-summary cards, one chart dataset and (Cash Flow) extra rows.

## Patch activation

`patches/financial_reports.apply()` replaces the module attributes:

| Module | Entry points |
|---|---|
| `erpnext.accounts.report.profit_and_loss_statement.profit_and_loss_statement` | `execute`, `execute_snapshot_report` |
| `erpnext.accounts.report.balance_sheet.balance_sheet` | `execute`, `execute_snapshot_report` |
| `erpnext.accounts.report.cash_flow.cash_flow` | `execute` |

It runs from `hooks.before_request` and `hooks.before_job`, so every web request and every RQ job
(prepared reports, scheduled report emails, `enqueue`) gets it. It is idempotent (a marker attribute
is looked up along the `__wrapped__` chain) and cheap.

Rules:

- `functools.wraps` keeps name, docstring and `__wrapped__` (the original is always reachable).
- An entry point that is missing or whose first parameter is not positional is **not** wrapped and
  is reported as `missing` / `incompatible` on *Financial Budget Settings → Integration status*.
- If another app already wrapped the function (`__wrapped__` present or a foreign `__module__`),
  we wrap the foreign wrapper (it still runs) and report `wrapped a foreign patch`.
- Python modules are shared by all sites served by one worker, so the decision is taken **per call**:
  a site without the app installed always gets the exact original result (verified against
  `dist2.local`, see `inspection.md`).

Paths not covered by the hooks: `bench execute`, `bench console` and scripts that call the report
module directly. Call `financial_budget.patches.financial_reports.apply()` first there.

## UI filters

`hooks.override_whitelisted_methods` overrides `frappe.desk.query_report.get_script`. The override
calls the previous override (or Frappe's own function, which keeps the report permission check) and,
only for the three reports and only when the integration is enabled, appends
`public/js/financial_report_budget.js` + `financial_budget.setup_report(name, config)`. That adds the
filters `show_budget`, `budget_source` (P&L) / `budget_scenario` (BS, CF), `budget_comparison`,
`show_variance`, `show_variance_pct`, `show_last_year`, `show_ytd` (P&L). Filters are de-duplicated by
fieldname. A later override of the same method is listed as a conflict on the settings page.

## Calculations

### Profit and Loss
- Budgets: submitted `Budget` documents of the company, `budget_against` = Cost Center (default) or
  Project, accounts from `Budget Account`.
- Each annual amount is split into fiscal-year months (Monthly Distribution percentages by month name,
  equal split when none) and pro-rated by days into each report period.
- No double counting: duplicate budgets for the same dimension/account/fiscal year are counted once
  (note shown), and when a group cost center and one of its descendants both budget an account, only
  the ancestor's budget is used (note shown).
- Group rows = sum of budgeted leaves below them. A leaf with no budget shows *empty*, not 0, status
  `No Budget`; a group with some unbudgeted leaves is `Partial Budget`.
- Net profit budget = income budget − expense budget; `Plan Incomplete` when one side has no budget.
- Variance = actual − budget; Variance % = variance / |budget| × 100 (empty when budget is 0 or missing).
- Last Year = the same report re-run for the range shifted by 12 months (actuals only).
- YTD Budget / YTD Actual = fiscal-year start → last period end, from the same budget segments and a
  single-range re-run of the original report.
- A report filter on the budget-source dimension limits the budgets to it (Cost Center filter → those
  cost centers and their children; Project filter → those projects). A filter on any other dimension
  (e.g. Project while the source is Cost Center, or an accounting dimension) hides the budget columns
  with a message, because those budgets cannot be split by it.
- Growth / Margin views are left untouched.

### Balance Sheet
- Needs *Accumulated Values* (balances). Company-level only: cost-center / project / dimension filters
  hide the budget columns with a message.
- Planned value at a period end = planned closing balance of the submitted Budgeted Balance Sheet for
  that date, or the plan's opening balance when the period ends the day before a plan period starts.
- A period with no plan is *empty* + message `Plan Incomplete`. A leaf with an actual balance but no plan
  row is `Not Planned`; its parents are `Plan Incomplete`.
- Provisional Profit / Loss = planned P&L of the plan (from ERPNext Budgets or entered manually).
  Total (Credit) = liabilities + equity + planned P&L. No balancing figure is ever invented: a plan can
  only be submitted when assets = liabilities + equity + planned P&L.

### Cash Flow (indirect method)
For each period [S, T] (S = ERPNext's own period start, fiscal-year start when accumulated):
- Profit = planned P&L YTD(T) − YTD(S−1).
- Depreciation = budget of accounts of type Depreciation in the period.
- Working capital / investing / financing lines = change in planned balance of the accounts ERPNext
  maps to that line (`get_cash_flow_accounts`): −Δ for assets, +Δ for liabilities/equity.
- Planned Adjustments = submitted Cash Flow Planning Adjustments in the period (signed cash effect;
  accounts already derived from the balance sheet — receivable, payable, stock, fixed asset, equity,
  cash, bank — are rejected so nothing is counted twice).
- Reconciliation rows: Planned Opening Cash, Net Change, Closing (Opening + Net Change), Cash per Balance
  Sheet Plan and Unreconciled Difference (`Reconciled` / `Unreconciled`). Any missing component makes
  the line and its totals *empty* with `Plan Incomplete: <components>`.

## Data model

| DocType | Purpose |
|---|---|
| Financial Budget Settings (single) | master switch, per-report switches, defaults, integration status |
| Budgeted Balance Sheet (submittable) + Budgeted Balance Sheet Account / Period | planned balances per account and period end, one submitted plan per company + fiscal year + scenario |
| Cash Flow Planning Adjustment (submittable) | planned cash items that are not balance-sheet movements |

Permissions: Accounts Manager / System Manager create and submit; Accounts User / Auditor read.
