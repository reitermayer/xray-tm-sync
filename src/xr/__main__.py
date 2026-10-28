import sys

from xr.cli import main

for stream in (sys.stdout, sys.stderr):
	stream.reconfigure(encoding="utf-8")  # Windows consoles default to a legacy code page

sys.exit(main())
