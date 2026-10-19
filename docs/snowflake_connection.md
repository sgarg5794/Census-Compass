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

## Server (service user) authentication

Status: deferred. Use the macOS Keychain access above for local development. The local key pair is generated; the Snowflake service user has not
been created or tested. Complete this setup before public deployment; see TODO.md.

### One-time Snowflake setup
Review `scripts/sql/create_service_user.sql` and run its contents in a Snowflake
worksheet as ACCOUNTADMIN. It creates CENSUS_COMPASS_SERVICE with TYPE=SERVICE,
registers the public key, and grants the existing CENSUS_READER role. The
application never receives ACCOUNTADMIN credentials. No SQL has been executed
by this preparation step.

### Key files
- `.secrets/snowflake_service.p8`: RSA 3072-bit private key, PKCS#8 PEM, permissions 0600.
- `.secrets/snowflake_service.pub`: corresponding public key.
- `.secrets/`: directory permissions 0700 and excluded from Git.

The private key is unencrypted for unattended authentication. Its contents are a
secret. Do not paste it into chat, commit it, or include it in a container image.
For deployment, store it in the hosting platform's secret manager and mount it as
a private file. The server must be given its own secret copy; the local path is
not automatically available on a server. No macOS Keychain dependency is needed.

### Application configuration
Set the following values locally or in deployment configuration:

```dotenv
SNOWFLAKE_ACCOUNT=DNPKSVD-ARC86250
SNOWFLAKE_USER=CENSUS_COMPASS_SERVICE
SNOWFLAKE_AUTHENTICATOR=SNOWFLAKE_JWT
SNOWFLAKE_PRIVATE_KEY_FILE=.secrets/snowflake_service.p8
SNOWFLAKE_ROLE=CENSUS_READER
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=US_OPEN_CENSUS_DATA_NEIGHBORHOOD_INSIGHTS_FREE_DATASET
SNOWFLAKE_SCHEMA=PUBLIC
```

On the server, PRIVATE_KEY_FILE must reference the mounted secret's server path.
The application connection module must map these settings to the Snowflake
connector and disable secondary roles. Its implementation and the validated query
tool are the next step; the current exploration helper continues using your
personal login and Keychain and does not read these service-user settings.

Reference: https://docs.snowflake.com/en/user-guide/key-pair-auth
