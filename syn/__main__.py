"""Lets ``py -m syn`` behave like the installed ``syn`` command."""

import sys

from .cli import main

sys.exit(main())
