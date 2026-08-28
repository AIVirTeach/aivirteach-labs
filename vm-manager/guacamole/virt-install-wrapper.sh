#!/bin/sh
set -eu

# The uv base image places its application Python before Debian's Python in
# PATH. Debian's virt-install uses PyGObject from /usr/lib/python3/dist-packages,
# so invoke it with the matching system interpreter while FastAPI continues to
# run on the image's /usr/local Python.
exec /usr/bin/python3 /usr/bin/virt-install "$@"
