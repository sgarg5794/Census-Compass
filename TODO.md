# Project to-do

## Database authentication

Current decision: use the existing macOS Keychain authentication for local agent development, with CENSUS_READER and secondary roles disabled. Server authentication is deferred.

- [ ] Before public deployment, create CENSUS_COMPASS_SERVICE using scripts/sql/create_service_user.sql.
- [ ] Store the private key in the deployment platform secret storage and configure server-side key-pair authentication.
- [ ] Verify service-user access, active role, query limits, and deployment connectivity.

The local key pair and SQL are prepared; the service user has not been created or tested. See docs/snowflake_connection.md.
