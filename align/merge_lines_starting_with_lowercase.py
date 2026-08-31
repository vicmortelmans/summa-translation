#!/usr/bin/env python3

import sys


def main() -> None:
    previous = None

    for raw_line in sys.stdin:
        line = raw_line.rstrip("\n")

        if not line:
            if previous is not None:
                print(previous)
                previous = None
            continue

        if previous is not None and line[:1].islower():
            previous = f"{previous} {line}"
        else:
            if previous is not None:
                print(previous)
            previous = line

    if previous is not None:
        print(previous)


if __name__ == "__main__":
    main()
