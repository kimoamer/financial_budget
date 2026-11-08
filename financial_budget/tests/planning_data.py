"""Shared, reconciling plan for the Balance Sheet and Cash Flow tests.

Actuals (FY 2026-2027): capital 1000 in cash, sales 2000, rent 250, equipment 3000 paid
1000 cash + 2000 loan.
Budgets: sales 12000, rent 2400, depreciation 1200 -> planned profit 8400.
Annual plan at 2027-06-30 (opening -> closing):
  cash 1000 -> 10100, debtors 0 -> 2000, equipment 0 -> 3000, acc. depreciation 0 -> -1200,
  creditors 0 -> 500, loan 0 -> 4000, capital 1000 -> 1000
  assets 13900 = liabilities 4500 + equity 1000 + planned profit 8400
"""

from financial_budget.tests import fixtures as fx
from financial_budget.tests.fixtures import acc

END = "2027-06-30"


def setup_actuals_and_budgets():
	fx.setup_company()
	fx.set_settings(enabled=1, enable_balance_sheet=1, enable_cash_flow=1)
	cash = acc("FB Cash")
	fx.journal("2026-07-01", [(cash, 1000, 0), (acc("FB Capital"), 0, 1000)])
	fx.journal("2026-07-10", [(cash, 2000, 0), (acc("FB Sales"), 0, 2000)])
	fx.journal("2026-07-15", [(acc("FB Rent"), 250, 0), (cash, 0, 250)])
	fx.journal("2026-08-01", [(acc("FB Equipment"), 3000, 0), (cash, 0, 1000), (acc("FB Loan"), 0, 2000)])
	fx.budget([(acc("FB Sales"), 12000), (acc("FB Rent"), 2400), (acc("FB Depreciation"), 1200)])


def plan_rows(cash_closing=10100, with_loan=True):
	rows = [
		(END, acc("FB Cash"), 1000, cash_closing - 1000),
		(END, acc("FB Debtors"), 0, 2000),
		(END, acc("FB Equipment"), 0, 3000),
		(END, acc("FB Acc Depreciation"), 0, -1200),
		(END, acc("FB Creditors"), 0, 500),
		(END, acc("FB Capital"), 1000, 0),
	]
	if with_loan:
		rows.append((END, acc("FB Loan"), 0, 4000))
	return rows
