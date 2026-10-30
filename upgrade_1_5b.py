#!/usr/bin/env python3
"""Run the shared, rollback-protected upgrade with a 1.5B resource budget."""
import os
from pathlib import Path
import sys
import upgrade_3b as upgrade


if __name__=='__main__':
    # Re-execute this entry point so sudo preserves the chosen model and budget.
    if os.geteuid()!=0:
        os.execvp('sudo',['sudo','python3',str(Path(__file__).resolve())])
    upgrade.MODEL='qwen2.5:1.5b'
    upgrade.AI_LIMIT=1400*upgrade.MIB
    upgrade.RESERVE=640*upgrade.MIB
    upgrade.cli()
