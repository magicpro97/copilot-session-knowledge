# Contributing to Copilot Session Knowledge

Thank you for your interest in contributing! This guide will help you get started.

## Reporting Bugs

1. [Open a GitHub issue](https://github.com/magicpro97/copilot-session-knowledge/issues/new)
2. Include: steps to reproduce, expected vs actual behavior, Python version, OS

## Suggesting Features

Open an issue with the `enhancement` label. Describe the use case and expected behavior.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/magicpro97/copilot-session-knowledge.git ~/.copilot/tools
cd ~/.copilot/tools

# No dependencies to install — pure stdlib Python 3.10+

# Run tests
python3 test_security.py    # 9 security tests
python3 test_fixes.py       # 65 tests
```

## Code Style

- **Pure stdlib Python 3.10+** — zero pip dependencies required
- **Each script is standalone** — no shared library imports between scripts
- **Parameterized SQL only** — all user input uses `?` placeholders, never string interpolation
- **Windows encoding fix** — every script starts with `os.name == "nt"` block for UTF-8 stdout/stderr
- **JSON serialization only** — never use pickle

## Testing

Run before every commit:

```bash
python3 test_security.py    # SQL injection, pickle, locks, paths
python3 test_fixes.py       # Noise filter, sub-agent, launchd, DB health
```

Verify syntax of modified files:

```bash
python3 -c "import ast; ast.parse(open('your_file.py').read())"
```

## Pull Request Process

1. Fork the repo and create a feature branch
2. Make your changes following the code style above
3. Run both test suites — no new failures allowed
4. Submit a PR with a clear description of the change

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting. Never commit secrets or API keys.
