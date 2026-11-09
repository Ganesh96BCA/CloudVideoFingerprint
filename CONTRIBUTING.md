# Contributing to Cloud Video Fingerprint Collector

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/CloudVideoFingerprint.git
   cd CloudVideoFingerprint
   ```
3. Create a virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Development Workflow

1. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes and commit with clear messages:
   ```bash
   git commit -m "Add description of changes"
   ```

3. Run tests to ensure nothing is broken:
   ```bash
   python -m pytest -q
   ```

4. Push to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```

5. Open a Pull Request with a clear description of the changes

## Code Style

- Follow PEP 8 conventions
- Use descriptive variable and function names
- Add docstrings to functions and classes
- Keep functions focused and modular

## Reporting Issues

- Check existing issues before opening a new one
- Provide a clear description of the issue
- Include steps to reproduce if applicable
- Include your Python version and OS

## Testing

- Write tests for new features
- Ensure all existing tests pass before submitting a PR
- Use pytest for testing

## Pull Request Process

1. Ensure your code passes all tests
2. Update documentation if needed
3. Provide a clear description of what the PR accomplishes
4. Link related issues (e.g., "Closes #123")

## Questions?

Feel free to open an issue for questions or discussions about the project.

Thank you for contributing! 🎉
