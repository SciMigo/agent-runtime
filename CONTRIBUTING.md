# Contributing to Agent Runtime

Thank you for your interest in contributing to Agent Runtime! This document provides guidelines and instructions for contributing.

## Getting Started

### Prerequisites

- Python 3.11 or higher
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- Git

### Development Setup

```bash
# Clone the repository
git clone https://github.com/SciMigo/agent-runtime.git
cd agent-runtime

# Create a virtual environment and install dependencies
uv venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows
uv pip install -e ".[dev]"

# Verify installation
agent-runtime --help
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=agent_runtime

# Run specific test file
pytest tests/test_server.py
```

### Code Quality

We use [ruff](https://github.com/astral-sh/ruff) for linting and formatting, and [mypy](https://mypy.readthedocs.io/) for type checking.

```bash
# Lint
ruff check .

# Format
ruff format .

# Type check
mypy src/agent_runtime
```

## How to Contribute

### Reporting Bugs

Before submitting a bug report:
1. Check the [existing issues](https://github.com/SciMigo/agent-runtime/issues) to avoid duplicates
2. Collect relevant information (OS, Python version, error messages, steps to reproduce)

When submitting a bug report, include:
- A clear, descriptive title
- Steps to reproduce the issue
- Expected vs actual behavior
- Relevant logs or error messages
- Your environment details

### Suggesting Features

Feature requests are welcome! Please:
1. Check existing issues and discussions first
2. Clearly describe the use case and motivation
3. Explain how it fits with the project's goals

### Submitting Pull Requests

1. **Fork the repository** and create your branch from `main`
2. **Make your changes** following our code style
3. **Add tests** for new functionality
4. **Run the test suite** to ensure nothing is broken
5. **Update documentation** if needed
6. **Submit a pull request** with a clear description

#### Pull Request Guidelines

- Keep PRs focused - one feature or fix per PR
- Write clear commit messages
- Include tests for new functionality
- Update relevant documentation
- Ensure CI passes before requesting review

### Commit Messages

We follow conventional commit style:

```
type(scope): description

[optional body]

[optional footer]
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `test`: Adding or updating tests
- `refactor`: Code changes that neither fix bugs nor add features
- `chore`: Maintenance tasks

Examples:
```
feat(kernel): add support for kernel interrupt
fix(auth): handle expired tokens gracefully
docs: update installation instructions
```

## Code Style

- Follow PEP 8 conventions
- Use type hints for all public functions
- Write docstrings for public modules, classes, and functions
- Keep functions focused and reasonably sized
- Prefer explicit over implicit

## Architecture Overview

```
src/agent_runtime/
├── api/           # FastAPI route handlers
├── kernels/       # Jupyter kernel management
├── auth.py        # Authentication and pairing
├── config.py      # Configuration management
├── envs.py        # Virtual environment management
├── events.py      # Event system
├── observability.py # Metrics and tracing
├── server.py      # FastAPI app setup
└── cli.py         # Command-line interface
```

## Questions?

- Open a [discussion](https://github.com/SciMigo/agent-runtime/discussions) for questions
- Check existing issues and discussions
- Read the [documentation](https://github.com/SciMigo/agent-runtime#readme)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
