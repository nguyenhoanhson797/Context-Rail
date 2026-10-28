"""``py -m context_rail`` entry point; the parser itself lives in ``cli``."""

from context_rail.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
