# Rollback

From fastest to most complete. Every level leaves ERPNext data (GL, Budgets) untouched.

## 1. Switch off (seconds, no restart)

*Financial Budget Settings*: untick **Enabled** (or one report switch). The wrappers stay installed
but return the original report result object unchanged, and the extra filters disappear on the next
report load. Users can also just untick **Show Budget**.

## 2. Remove the wrappers from running processes

```python
from financial_budget.patches.financial_reports import remove
remove()   # unwraps only our own top-level wrappers; a foreign wrapper above ours is left alone
```

This only affects the current process; the hooks re-apply on the next request/job while the app is
installed. Use level 1 or 3 for a lasting rollback.

## 3. Uninstall from a site

```bash
bench --site <site> uninstall-app financial_budget      # before_uninstall calls remove()
sudo supervisorctl restart all
```

Uninstall deletes the app's DocTypes **and their records** (Budgeted Balance Sheet, Cash Flow Planning
Adjustment, settings). Export them first if the plans must be kept:

```bash
bench --site <site> export-csv "Budgeted Balance Sheet" bbs.csv
bench --site <site> export-csv "Cash Flow Planning Adjustment" cfa.csv
# or: bench --site <site> backup
```

ERPNext Budgets and Monthly Distributions belong to ERPNext and are kept.

## 4. Remove from the bench

```bash
bench remove-app financial_budget      # only after it is uninstalled from every site
sudo supervisorctl restart all
```

Other sites on the bench are never affected by the wrappers (the per-call site check returns the
original result), but they do need the restart after the package is added or removed.
