# Local Snowflake access

Account: `DNPKSVD-ARC86250`. User: `SAHILGARG5794`.
Role: `CENSUS_READER`. Warehouse: `COMPUTE_WH`.
Database: `US_OPEN_CENSUS_DATA_NEIGHBORHOOD_INSIGHTS_FREE_DATASET`, schema `PUBLIC`.

Run this once in your own macOS Terminal:

```sh
cd /Users/sahilgarg/Desktop/Census-Compass
.venv/bin/python scripts/snowflake_access.py --setup
```

Enter your Snowflake password at the hidden prompt. Approve MFA if requested.
The script tests access, checks the active role and the expected 3,234 FIPS rows,
then saves the password in macOS Keychain. It does not write the password to
project files or print it. macOS may ask you to approve Keychain access.

For accounts requiring the connector's explicit username/password MFA mode:

```sh
CENSUS_SNOWFLAKE_AUTHENTICATOR=username_password_mfa .venv/bin/python scripts/snowflake_access.py --setup
```

Authentication still depends on the account's authentication policy. Report
error codes without sharing your password if setup fails.

After setup, the agent can run the default connection check:

```sh
.venv/bin/python scripts/snowflake_access.py
```

Or execute a query, with at most 100 result rows returned by default:

```sh
.venv/bin/python scripts/snowflake_access.py --sql 'SELECT CURRENT_ROLE()'
```

`--file` accepts a file containing one query. `--limit` controls returned rows
(1–10,000); it does not limit rows scanned or query cost. Statements have a
45-second server timeout. Secondary roles are disabled on every connection.
This is an exploration helper, not the production agent's SQL validation layer.
Database permissions remain the authority for allowed operations.

Credentials are stored under the Keychain service
`census-compass-snowflake:DNPKSVD-ARC86250`. They can be removed using macOS
Keychain Access. The agent has not connected until the live connection check
succeeds after local setup.
