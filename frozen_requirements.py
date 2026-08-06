"""Import manifest for the frozen build. NEVER imported at runtime.

The bot engine's code ships as plain .py files NEXT TO the exe and runs from
disk (see shell.py's dispatch shim), so PyInstaller's analysis never sees its
imports. Everything bot/ needs from site-packages or the stdlib must be listed
here, or the frozen engine dies on an ImportError the dev run never hits.

Listed = bundled. Keep in sync with bot/src imports when the engine grows one.
"""
# pip packages the disk-run engine imports
import flask            # noqa: F401  bot/src/api.py
import requests         # noqa: F401  bot/src/alpaca_client.py
import tzdata           # noqa: F401  zoneinfo's database on Windows

# stdlib the disk-run engine imports (PyInstaller bundles only what is traced)
import argparse                     # noqa: F401
import csv                          # noqa: F401
import html                         # noqa: F401
import math                         # noqa: F401
import sqlite3                      # noqa: F401  bot/src/db.py
import statistics                   # noqa: F401
import uuid                         # noqa: F401
import xml.etree.ElementTree        # noqa: F401  bot/src/reddit.py
import zoneinfo                     # noqa: F401  bot/run_bot.py scheduler
from datetime import datetime       # noqa: F401
