# Security

Report security issues privately to the repository owner. Do not open public
issues for vulnerabilities, secrets, or supply-chain concerns.

This module never needs provider credentials in source. Credentials, OAuth
state, runtime logs, and user model-provider settings are target-local runtime
state and must not be committed.
