# CViche — SAML Integration Request (Production)

Form-ready content for filing at:

> WCM ITS Service Portal → IT Service Catalog → Accounts and Access → **SAML Integration Request**

Drafted 2026-05-20. Filed 2026-05-20 as **RITM0791615**.

---

## Form fields

| Field | Value |
|---|---|
| Existing or new SAML integration? | **New** |
| Production or non-production? | **Production only** (Non-Production deferred until a separate staging hostname is provisioned) |
| Application name | `CViche` |
| Short description | CV parsing pipeline with web interface for Weill Cornell Medicine faculty. SSO gates every authenticated surface — CV upload, dashboard, settings. No public-facing read access. |
| Application home page URL | `https://cviche.weill.cornell.edu/` |
| Institutions | **Weill Cornell Medicine (WCM), Weill Cornell Medicine — Qatar (WCM-Q), NewYork-Presbyterian (NYP)** |
| SAML proxy service | **Yes** (multi-institution; the proxy normalizes attribute release across the three source IdPs) |
| Technical contact | Paul Albert, paa2013@med.cornell.edu |
| Administrative contact | Paul Albert, paa2013@med.cornell.edu |
| SAML entity ID | `https://cviche.weill.cornell.edu/shibboleth` |
| Can app natively do its own authorization? | **Yes** |
| Need IdP to do authorization on behalf of app? | **No** — the IdP only authenticates; CViche enforces authorization in two layers (Enterprise Directory group lookup at login, app-native rate limits/roles after that). |
| Data classification | **Moderate** — CVs contain personally identifiable employment, education, and publication history but no PHI or FERPA-protected data. |
| Default DUO policy sufficient? | **Yes** |
| Encrypt assertion? | **Yes** |
| NameID format | **Email Address — eduPersonPrincipalName (recommended)** — NameID format is cosmetic; CViche reads `mail` directly from the attribute statement. |
| ACS URL | `https://cviche.weill.cornell.edu/api/saml/acs` |
| ACS endpoint binding | **HTTP-POST only** |
| SLO URL | _(blank — explained in Request Details: not requested for v1)_ |
| SLO endpoint binding | _(uncheck everything)_ |
| Public x509 certificate | _(paste the contents of the certificate PEM block below, including the BEGIN/END CERTIFICATE lines)_ |
| Additional attributes | `mail` (required); `displayName` and `eduPersonPrincipalName` are optional and used for display fallback only. The default attribute set can flow through. |
| SAML metadata XML | _(blank; served at `https://cviche.weill.cornell.edu/api/saml/metadata` once the SP is in saml mode — happy to attach a pre-deploy static export from a local run if needed)_ |
| Request Details | _(paste the block below)_ |

---

## Request Details block (paste into the multi-line field)

```
Scope: Production only — cviche.weill.cornell.edu. CViche runs on the `reciter`
EKS cluster (us-east-1) in the `cviche-dev` namespace, behind an internal ALB.
A parallel non-production registration is deferred until a separate staging
hostname is provisioned.

SSO purpose: CViche is an internal CV parsing tool for Weill Cornell Medicine
faculty. SSO gates every authenticated surface — CV upload, dashboard, and
settings. No public-facing read access.

Population: WCM + WCM-Q + NYP. The app reads only the user's `mail` attribute
(urn:oid:0.9.2342.19200300.100.1.3) and joins on email. Optionally consumes
`displayName` (urn:oid:2.16.840.1.113730.3.1.241) and `eduPersonPrincipalName`
(urn:oid:1.3.6.1.4.1.5923.1.1.1.6) for display fallback only.

Authorization is enforced entirely in the app, in two layers:
(1) Enterprise Directory tier — per-login LDAPS query against
    cn=ITS:Library:CViche/user-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu (access)
    and
    cn=ITS:Library:CViche/admin-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu (admin).
    Membership cache TTL is 5 minutes; the next request after group removal
    enforces revocation. These ED groups will be provisioned as part of this
    request — please flag if a separate Enterprise Directory ticket is needed.
(2) App-native tier (rate limits, role escalations, consent versioning) —
    in the app's MySQL DB.

Group memberships are deliberately NOT requested via SAML claim — a stale
group claim in a multi-hour session would risk admin surfaces persisting
past a real revocation.

SLO not requested for v1. Logout is handled by a local /api/saml/logout
endpoint that clears the session cookie. We can add IdP SLO later if needed.

SP metadata XML will be served at https://cviche.weill.cornell.edu/api/saml/metadata
once the SP is in saml mode. Happy to attach a pre-deploy static export from a
local run if needed.

What we need back:
  1. IdP metadata URL (pysaml2 fetches IdP entity ID, SSO URL, and signing
     cert from this XML)
  2. Confirmation that `mail` carries the user's real email address (e.g.,
     js1234@med.cornell.edu) regardless of source IdP through the SAML proxy
  3. Confirmation of any prior CViche IdP-side configuration — a teammate
     (Mahender, mrj4001@med.cornell.edu) may have started one earlier on the
     EKS-side; reconcile to this filing's entity ID if any prior entry exists
  4. WCM WAYF discovery service URL to populate the SP's discovery_url config
```

---

## SP Certificate (paste into the "Public x509 certificate" field)

- **Subject:** `C=US, ST=NY, L=New York, O=Weill Cornell Medicine, OU=CViche, CN=cviche.weill.cornell.edu`
- **Valid from:** 2026-05-20 14:31:50 UTC
- **Valid to:** 2029-05-19 14:31:50 UTC (3-year validity)
- **SHA-256 fingerprint:** `5B:C2:43:CD:76:CC:21:B2:DD:D8:1E:B2:34:44:51:73:F1:AC:3C:DD:65:01:B0:2B:1F:42:0A:A2:B6:B8:1A:47`
- **Key algorithm:** RSA 2048-bit

```
-----BEGIN CERTIFICATE-----
MIID5zCCAs+gAwIBAgIUJuyQOoWgTKRwUlHzQ3v4m+KCfhcwDQYJKoZIhvcNAQEL
BQAwgYIxCzAJBgNVBAYTAlVTMQswCQYDVQQIDAJOWTERMA8GA1UEBwwITmV3IFlv
cmsxHzAdBgNVBAoMFldlaWxsIENvcm5lbGwgTWVkaWNpbmUxDzANBgNVBAsMBkNW
aWNoZTEhMB8GA1UEAwwYY3ZpY2hlLndlaWxsLmNvcm5lbGwuZWR1MB4XDTI2MDUy
MDE0MzE1MFoXDTI5MDUxOTE0MzE1MFowgYIxCzAJBgNVBAYTAlVTMQswCQYDVQQI
DAJOWTERMA8GA1UEBwwITmV3IFlvcmsxHzAdBgNVBAoMFldlaWxsIENvcm5lbGwg
TWVkaWNpbmUxDzANBgNVBAsMBkNWaWNoZTEhMB8GA1UEAwwYY3ZpY2hlLndlaWxs
LmNvcm5lbGwuZWR1MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAw428
95eG+W37RuyN1qFRyrEWkieguapjcsuFCV3hyEoIr5fDPwqF3mwNziVBu+rjVruR
j9H4YPgpPwGz8hurIoVRkYmnehxPvH0nrxgkCcJtHIcmw4kMML81hwIzPhHDq6Ip
d40kpgDmVRzEQRuxZP5XU8bF0kU7I/YEnUyHhyd59d7oC80UzDicVcqEznSwC3BW
zK62F5HTLByZ+iBBI9W9LHUGATKHyAlkqu6DsE/X2Kk7lHyLERkB15+qtpsWDFZi
RiPH4MsKVvNgOuo2cGRopj188k+t+AIslTpHgCI8cQFwENOech/OBOt41f5vhBem
k6pzu8iES25rZxq3XwIDAQABo1MwUTAdBgNVHQ4EFgQUQMCpbU/+qwFeger/C4I6
02S5BqEwHwYDVR0jBBgwFoAUQMCpbU/+qwFeger/C4I602S5BqEwDwYDVR0TAQH/
BAUwAwEB/zANBgkqhkiG9w0BAQsFAAOCAQEAPmw7s79pd/p5+yZg46RpvcrT/8QG
2C2cKgE8nYg4pcPhgS3wpHAXlOed9ReSy+KHLLaYjygFfXfY7vGAP4DFk6Q2t5PS
ftSe6/6jePutd+/m03xAmzUz/QfuQGEAM5blXWbjSQpwwB5nQ+jlZvVQcMQdnLo7
KT+yQvU2FcTADdlcK8S9xNpliQHCzHcLRDAn4DoJ4+ZX6wk+IfjV8XOCxmCnXy4c
CvkBSjRflGiSj4XcZYzt7TLRvwcV08SFr0/965ekyAxqFXLWDdbK5j/M6jZWoWfm
VZR1s4q9s7yAfUZmoay3iJ7obrAWTmzF5OaQhWkc2bVovmHGLtVRDvVSag==
-----END CERTIFICATE-----
```

---

## Pre-submission checklist

- [x] Private key (`~/cviche-saml-sp-prod.key.pem`, mode 600) saved into 1Password as **Secure Note**: `CViche — SAML SP private key (prod)`. Plaintext key removed from disk 2026-05-20.
- [ ] (Optional, recommended) Private key also stored in AWS Secrets Manager at `cviche/saml-sp/prod/private-key` in `us-east-1` (account 665083158573).
- [x] ED groups `ITS:Library:CViche/user-role` and `ITS:Library:CViche/admin-role` provisioned in WCM Enterprise Directory under `ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu` (2026-05-20).
- [x] Backend pod is on a build that contains alembic head `d7a4f9b2e103` and the cviche-dev deployment is healthy (image `dev-70`, 0 restarts, verified 2026-05-20).
- [ ] Calendar reminder set for Q1-2029 cert rotation (3-year validity, expires 2029-05-19).

## After filing

1. Record the ticket number at the top of this file.
2. When IT responds, follow `docs/sp-registration.md` → "Post-Registration: Wiring IdP Values" in the CViche repo.
3. Smoke-test the SAML flow against the real IdP before announcing internal availability.
