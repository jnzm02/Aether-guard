# Contributing to Aether-Guard

Thank you for considering contributing to Aether-Guard! This document provides guidelines for contributing to the project.

## Getting Started

1. **Fork the repository** and clone it locally
2. **Set up your development environment** following the README Quick Start section
3. **Create a feature branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Running Tests Locally

Before submitting a PR, ensure all tests pass:

### Go Tests (target-service, event-tracker)
```bash
# Run all Go tests with race detection
cd services/target-service && go test -race ./...
cd services/event-tracker && go test -race ./...
```

### Python Tests (agent, listener)
```bash
# Run agent tests (239 tests)
python3 -m pytest services/agent/tests/ -v

# Run listener tests (14 tests)
python3 -m pytest services/listener/tests/ --import-mode=importlib -v

# Run with coverage report
python3 -m pytest services/agent/tests/ --cov=services/agent --cov-report=term-missing
```

### Infrastructure Validation
```bash
# Validate Prometheus config
promtool check config infra/prometheus/prometheus.yml
promtool check rules infra/prometheus/rules/*.yml

# Validate Alertmanager config
amtool check-config infra/alertmanager/alertmanager.yml
```

## Code Style

### Python
- **Linting**: We use `ruff` for linting and formatting
  ```bash
  ruff check services/agent/ services/listener/ scripts/
  ruff format services/agent/ services/listener/ scripts/
  ```
- **Style**: Follow PEP 8 conventions
- **Type hints**: Use type hints for function signatures
- **Docstrings**: Add docstrings for public functions and classes

### Go
- **Formatting**: Use `gofmt` (enforced in CI)
  ```bash
  go fmt ./...
  ```
- **Linting**: Use `go vet`
  ```bash
  go vet ./...
  ```
- **Naming**: Follow Go naming conventions (camelCase for private, PascalCase for public)

## Pull Request Process

1. **Ensure all tests pass** locally before pushing
2. **Write clear commit messages** following conventional commits format:
   - `feat:` for new features
   - `fix:` for bug fixes
   - `docs:` for documentation changes
   - `test:` for test additions/modifications
   - `refactor:` for code refactoring
   - `chore:` for maintenance tasks

3. **Update documentation** if you're adding new features or changing behavior
4. **Add tests** for any new functionality
5. **Keep PRs focused** - one feature or fix per PR
6. **Fill out the PR template** with:
   - Description of changes
   - Motivation and context
   - Testing performed
   - Screenshots (if UI changes)

## Areas for Contribution

### Priority Areas
- **Rule patterns**: Add new deterministic patterns to `services/agent/rules.py`
- **Policy improvements**: Expand the policy matrix in `services/agent/policy.py`
- **Integration tests**: Add end-to-end test scenarios
- **Documentation**: Improve runbooks, architecture docs, or examples
- **Grafana dashboards**: Create visualizations for trust metrics and cost tracking

### Good First Issues
Look for issues tagged with `good-first-issue` in the GitHub issue tracker.

## Reporting Bugs

When reporting bugs, please include:
- **Environment details**: OS, Docker version, Python/Go version
- **Steps to reproduce**: Detailed steps to reproduce the issue
- **Expected behavior**: What you expected to happen
- **Actual behavior**: What actually happened
- **Logs**: Relevant log excerpts (use code blocks)
- **Configuration**: Relevant parts of your `.env` or config files (redact secrets!)

## Feature Requests

For feature requests, please:
- **Search existing issues** to avoid duplicates
- **Describe the use case** - why is this feature needed?
- **Propose a solution** if you have ideas on implementation
- **Consider alternatives** - are there existing features that could be adapted?

## Code of Conduct

- Be respectful and inclusive
- Focus on constructive feedback
- Assume good intentions
- Help others learn and grow

## Questions?

If you have questions, feel free to:
- Open a GitHub Discussion
- File an issue with the `question` label
- Check existing documentation in `docs/`

Thank you for contributing to Aether-Guard!
