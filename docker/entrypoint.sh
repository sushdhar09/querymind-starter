#!/bin/sh
set -eu

case "${ANALYTICS_BACKEND:-azure}" in
    azure)
        if [ -z "${AZURE_SQL_CONNECTION_STRING:-}" ]; then
            echo "AZURE_SQL_CONNECTION_STRING is not set. Put the app's read-only connection string in .env, or set ANALYTICS_BACKEND=memory for an offline demo." >&2
            exit 1
        fi
        ;;
    memory)
        echo "ANALYTICS_BACKEND=memory: using the in-memory test double, not Azure SQL." >&2
        ;;
    *)
        echo "Unknown ANALYTICS_BACKEND '${ANALYTICS_BACKEND}' (expected azure or memory)." >&2
        exit 1
        ;;
esac

exec "$@"
