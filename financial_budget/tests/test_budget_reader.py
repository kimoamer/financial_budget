from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from financial_budget.financial_budget.budget_periods import allocate, budget_segments, fiscal_year_months
from financial_budget.tests import fixtures as fx
from financial_budget.tests.fixtures import acc


class TestBudgetPeriods(FrappeTestCase):
	def test_custom_fiscal_year_months(self):
		months = fiscal_year_months("2026-04-06", "2027-04-05")  # UK-style fiscal year
		self.assertEqual(len(months), 12)
		self.assertEqual(months[0], (getdate("2026-04-06"), getdate("2026-05-05")))
		self.assertEqual(months[-1][1], getdate("2027-04-05"))
		# month-end anchoring does not drift (Jan 31 -> Feb 28 -> Mar 31)
		jan = fiscal_year_months("2026-01-31", "2026-12-30")
		self.assertEqual(jan[1], (getdate("2026-02-28"), getdate("2026-03-30")))

	def test_equal_split_and_distribution(self):
		equal = budget_segments(1200, "2026-07-01", "2027-06-30")
		self.assertAlmostEqual(sum(s[2] for s in equal), 1200)
		self.assertAlmostEqual(equal[0][2], 100)
		dist = budget_segments(1000, "2026-07-01", "2027-06-30", {"July": 40, "December": 60})
		self.assertAlmostEqual(dist[0][2], 400)
		self.assertAlmostEqual(dist[5][2], 600)
		self.assertAlmostEqual(sum(s[2] for s in dist), 1000)

	def test_long_fiscal_year_never_exceeds_budget(self):
		segs = budget_segments(1300, "2026-01-01", "2027-01-31", {m: 100 / 12 for m in ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")})
		self.assertEqual(len(segs), 13)
		self.assertAlmostEqual(sum(s[2] for s in segs), 1300)

	def test_allocation_prorates_partial_months(self):
		segs = budget_segments(3100, "2026-07-01", "2027-06-30")  # July = 3100/12
		july = 3100 / 12
		self.assertAlmostEqual(allocate(segs, "2026-07-01", "2026-07-31"), july)
		self.assertAlmostEqual(allocate(segs, "2026-07-16", "2026-07-31"), july * 16 / 31)
		self.assertAlmostEqual(allocate(segs, "2026-01-01", "2028-01-01"), 3100)
		self.assertEqual(allocate(segs, "2025-01-01", "2025-12-31"), 0)


class TestBudgetReader(fx.BudgetTestCase, FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fx.setup_company()

	def test_only_submitted_budgets_of_company(self):
		from financial_budget.financial_budget.budget_reader import get_budget_segments

		fx.budget([(acc("FB Rent"), 1200)])
		draft = fx.budget([(acc("FB Salaries"), 999)])
		draft.cancel()
		segments, _notes = get_budget_segments(fx.COMPANY, "Cost Center", "2026-07-01", "2027-06-30")
		self.assertEqual(set(segments), {acc("FB Rent")})
		self.assertAlmostEqual(allocate(segments[acc("FB Rent")], "2026-07-01", "2027-06-30"), 1200)
		other, _n = get_budget_segments("FB Test Co 2", "Cost Center", "2026-07-01", "2027-06-30")
		self.assertEqual(other, {})

	def test_plan_index_opening_and_closing(self):
		from financial_budget.financial_budget.budget_reader import PlanIndex
		from financial_budget.tests import planning_data as pdata

		fx.budget([(acc("FB Sales"), 12000), (acc("FB Rent"), 2400), (acc("FB Depreciation"), 1200)])
		fx.plan(pdata.plan_rows())
		index = PlanIndex(fx.COMPANY, "Base")
		self.assertEqual(index.balances("2026-06-30")[acc("FB Cash")], 1000)  # opening of the plan period
		self.assertEqual(index.balances("2027-06-30")[acc("FB Cash")], 10100)
		self.assertIsNone(index.balances("2026-12-31"))
		self.assertEqual(index.profit_loss[getdate("2027-06-30")], 8400)
