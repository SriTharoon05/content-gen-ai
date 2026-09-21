# Backend

See the root [README](../README.md) and [production deployment guide](../PRODUCTION.md).

Run commands from this directory so `.env` resolves correctly. Start one Uvicorn process; it owns one background worker. Do not start a second worker against the same deployment.
