"""Read GARMIN_EMAIL + GARMIN_PASSWORD from .env and print SAFE diagnostics.

We never print the actual password. We only print:
  - length
  - whether it contains commonly-problematic characters for .env parsing
  - first and last character ordinals (helps spot stray whitespace / hidden chars)
"""
import sys
from pathlib import Path

# Add project root to sys.path so we can import the `backend` package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config


def main() -> None:
    email = config.GARMIN_EMAIL
    pw = config.GARMIN_PASSWORD

    print("EMAIL")
    print(f"  length:           {len(email)}")
    print(f"  has trailing space: {email != email.rstrip()}")
    print(f"  has leading space:  {email != email.lstrip()}")

    print("\nPASSWORD")
    print(f"  length:           {len(pw)}")
    print(f"  has spaces:       {' ' in pw}")
    print(f"  has tab:          {chr(9) in pw}")
    print(f"  has '#':          {'#' in pw}")
    print(f"  has dollar sign:  {chr(36) in pw}")
    print(f"  has backslash:    {chr(92) in pw}")
    print(f"  has double quote: {chr(34) in pw}")
    print(f"  has single quote: {chr(39) in pw}")
    print(f"  has = sign:       {'=' in pw}")
    print(f"  starts with quote: {pw.startswith(chr(34)) or pw.startswith(chr(39))}")
    print(f"  ends with quote:   {pw.endswith(chr(34)) or pw.endswith(chr(39))}")
    if pw:
        print(f"  first char ord:   {ord(pw[0])}")
        print(f"  last char ord:    {ord(pw[-1])}")


if __name__ == "__main__":
    main()
