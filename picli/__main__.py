"""Stable entry point: python -m picli."""
from picli.cli.main import main

if __name__ == '__main__':
    import sys
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        sys.exit(130)
