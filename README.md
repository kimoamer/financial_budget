### Financial Budget

Budgets, variances and planned statements inside the standard ERPNext financial reports.

See [docs/architecture.md](docs/architecture.md), [docs/installation.md](docs/installation.md), [docs/rollback.md](docs/rollback.md) and [docs/inspection.md](docs/inspection.md).

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app financial_budget
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/financial_budget
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
