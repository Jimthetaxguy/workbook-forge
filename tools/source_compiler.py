"""Compatibility launcher; the standalone compiler ships in the library wheel."""
from workbook_forge import source_cli as _implementation


def __getattr__(name):
    return getattr(_implementation,name)


def main(argv=None):
    return _implementation.main(argv,default_profile="numeric-v1")


if __name__ == "__main__":
    raise SystemExit(main())
