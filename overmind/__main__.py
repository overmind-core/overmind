"""Allow ``python -m overmind …`` when a different ``overmind`` script shadows PATH."""

from overmind.cli import main

if __name__ == "__main__":
    main()
