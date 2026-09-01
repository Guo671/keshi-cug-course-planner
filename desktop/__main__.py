"""Allow ``python -m desktop`` during development and diagnostics."""

from .launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
