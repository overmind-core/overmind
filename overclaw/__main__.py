"""Allow ``python -m overclaw …`` when a different ``overclaw`` script shadows PATH."""

from overclaw.cli import main

if __name__ == "__main__":
    main()
