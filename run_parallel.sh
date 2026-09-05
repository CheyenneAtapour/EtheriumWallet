#!/bin/sh
# exec so the python process replaces the shell: signals sent to this script
# reach python directly instead of stopping at the shell and orphaning workers.
exec .venv/bin/python ethwalletmaker_fast.py "$@"
