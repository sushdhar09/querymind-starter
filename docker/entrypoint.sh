#!/bin/sh
set -eu

if [ ! -f "$DB_PATH" ]; then
    if [ "${SEED_ON_START:-true}" = "true" ]; then
        echo "Analytics database not found at $DB_PATH; seeding the demo e-commerce dataset"
        python data/seed_db.py
    else
        echo "Analytics database not found at $DB_PATH and SEED_ON_START is not true" >&2
        exit 1
    fi
fi

exec "$@"
