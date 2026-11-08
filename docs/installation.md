# Installation

Requires Frappe v15 and ERPNext v15 (verified on 15.121.6; the patched report files are identical to
the v15.124.1 tag).

```bash
cd ~/frappe-bench
bench get-app <repo-url> --branch develop        # or the local path
bench --site <site> install-app financial_budget
bench --site <site> migrate
sudo supervisorctl restart all                    # required, see below
```

**Restart is mandatory.** Production gunicorn runs with `--preload`; processes started before the
package was pip-installed cannot import `financial_budget` and return HTTP 500
(`ModuleNotFoundError`) on every site whose hook cache is rebuilt — including sites that do not have
the app. Restart web *and* workers right after `get-app`.

## After install

1. *Financial Budget Settings*: `Enabled` and the three report switches are on by default.
   The *Integration status* section must list every entry point as `wrapped`.
2. P&L: create and submit an ERPNext **Budget** (Cost Center or Project, optional Monthly Distribution).
3. Balance Sheet / Cash Flow: create a **Budgeted Balance Sheet** for the fiscal year (scenario `Base`),
   fill planned opening + movement per balance-sheet leaf, and submit it (it must balance).
4. Optional: **Cash Flow Planning Adjustment** for planned cash items that are not balance movements
   (e.g. loan drawdown booked to an account with no account type).
5. Open a report and tick **Show Budget**.

Users need read permission on Budget (P&L), Budgeted Balance Sheet (BS) and all three (Cash Flow);
otherwise the report shows the actuals with a message instead of budget columns.

## CLI / scripts

`before_request` / `before_job` do not run in `bench execute`, `bench console` or custom scripts.
Call this first there:

```python
from financial_budget.patches.financial_reports import apply
apply()
```

## Checks

```bash
bench --site <site> run-tests --module financial_budget.tests.test_monkey_patch
bench --site <site> run-tests --module financial_budget.tests.test_profit_and_loss
bench --site <site> run-tests --module financial_budget.tests.test_balance_sheet
bench --site <site> run-tests --module financial_budget.tests.test_cash_flow
bench --site <site> run-tests --module financial_budget.tests.test_budget_reader
```

Tests create their own company (`FB Test Co`) and roll everything back.
