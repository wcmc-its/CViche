# RDS CA bundle

Source: https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
sha256: e5bb2084ccf45087bda1c9bffdea0eb15ee67f0b91646106e466714f9de3c7e3
Fetched: 2026-09-05
Re-fetch when AWS rotates RDS CAs.

## Re-vendoring

The checksum above is no longer documentation only: `app/database_factory.py`
pins the same digest in `RDS_CA_SHA256` and verifies it on every IAM-mode
engine build, refusing to start on a mismatch. Replacing the PEM therefore
means updating **three** things in the same commit:

1. `rds-global-bundle.pem` itself,
2. the `sha256:` line above,
3. `RDS_CA_SHA256` in `web_interface/backend/app/database_factory.py`.

`shasum -a 256 web_interface/backend/rds_ca/rds-global-bundle.pem` produces the
value for 2 and 3. `test_rds_ca_bundle_matches_pinned_checksum` (in
`web_interface/backend/tests/test_database_factory.py`) fails the build if the
file and the constant drift apart, so a half-done re-vendor cannot merge.
