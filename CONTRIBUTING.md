# Contributing to panoramaSlice

Thank you for taking the time to contribute!

## Getting started

```bash
git clone https://github.com/JimmyMtl/panoramaSlice.git
cd panoramaSlice
uv sync --extra dev
uv run python -m pytest tests/ -v
```

## How to contribute

- **Bug reports** — open an issue with steps to reproduce, your OS/Python version, and the error output.
- **Feature requests** — open an issue describing the use case before writing code.
- **Pull requests** — keep them focused; one feature or fix per PR. Add or update tests when behaviour changes.

## Code style

- Type hints on all public functions.
- No comments explaining *what* the code does — only *why* when non-obvious.
- Run tests before opening a PR: `uv run python -m pytest tests/`.

## Project layout

```
insta_pano/
├── config.py      # constants and defaults
├── processor.py   # image pipeline (resize → crop → slice → export)
├── utils.py       # validation and logging helpers
├── main.py        # CLI entry point
├── gui.py         # tkinter GUI
└── photo_picker.py # Apple Photos + local folder pickers
tests/
└── test_processor.py
```

## Commit messages

Short imperative present tense: `add HEIC support`, `fix button staying disabled`.

## License

By contributing you agree that your code will be licensed under the [MIT License](LICENSE).
