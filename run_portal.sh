#!/bin/sh
cd "$(dirname "$0")"
exec python3 business_scrying_portal.py "$@"
