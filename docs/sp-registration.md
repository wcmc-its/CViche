# CViche SAML SP Registration Guide

This document provides the information needed to register CViche as a SAML 2.0 Service Provider with the Weill Cornell Medicine Identity Provider.

## SP Metadata

CViche exposes a standard SAML 2.0 SP metadata endpoint for automated import:

- **Metadata URL:** `https://<cviche-host>/api/saml/metadata`
- **Entity ID:** `https://cviche.med.cornell.edu/shibboleth`

The metadata endpoint returns XML conforming to the SAML 2.0 metadata specification. It is only available when CViche is running in SAML authentication mode.

The entity ID is configurable in CViche's `auth_config.yaml` under the `saml.entity_id` field. The value registered with the IdP must match the configured entity ID exactly.

## Required SAML Attributes

CViche expects the following attributes in the SAML assertion. OID format is preferred for interoperability; friendly names are also accepted.

| Attribute | OID | Example Value | Required |
|-----------|-----|---------------|----------|
| mail | `urn:oid:0.9.2342.19200300.100.1.3` | `user@med.cornell.edu` | Yes |
| displayName | `urn:oid:2.16.840.1.113730.3.1.241` | `Jane Smith` | No |
| eduPersonPrincipalName | `urn:oid:1.3.6.1.4.1.5923.1.1.1.6` | `js1234@cornell.edu` | No |

**Notes:**

- `mail` is the only required attribute. If it is missing from the assertion, login will fail with a clear error message.
- `displayName` is used to populate the user's display name. If absent, CViche falls back to `eduPersonPrincipalName`, then to `mail`.
- `eduPersonPrincipalName` (ePPN) is a scoped identifier, not an email address. CViche uses `mail` for user identification, not ePPN.

## SAML Endpoints

| Endpoint | URL | Binding |
|----------|-----|---------|
| Assertion Consumer Service | `https://<cviche-host>/api/saml/acs` | HTTP-POST |
| Single Logout Service | `https://<cviche-host>/api/saml/logout` | HTTP-Redirect |
| SP Metadata | `https://<cviche-host>/api/saml/metadata` | HTTP-GET |

The Assertion Consumer Service (ACS) endpoint receives the signed SAML assertion from the IdP via HTTP-POST binding. The logout endpoint clears the local session only; there is no IdP Single Logout round-trip.

## Discovery Service Integration

CViche uses the WCM discovery service (WAYF) at `login.weill.cornell.edu` for IdP selection.

- The discovery service URL is configured in CViche's `auth_config.yaml` under `saml.discovery_url`.
- When a user clicks "Sign in with SSO", CViche constructs a SAML AuthnRequest via pysaml2 and redirects the user to the IdP. The discovery service URL is referenced by the frontend for informational display only -- the actual SAML redirect is handled by the backend.
- The `SIMPLESAMLPHP_SP_ENTITY_ID` registered with the discovery service must match CViche's configured entity ID (`https://cviche.med.cornell.edu/shibboleth`).

## SP Certificate

CViche auto-generates a self-signed SP certificate on first startup:

- **Algorithm:** RSA 2048-bit
- **Validity:** 10 years
- **Files:** `sp.crt` and `sp.key` in the configured `saml.cert_dir` directory (default: `certs/`)
- **Format:** PEM (TraditionalOpenSSL for the private key)

If the IdP requires a specific certificate or key pair, replace `sp.crt` and `sp.key` with the desired files. CViche will use whatever certificate files are present in the configured directory.

## Enterprise Directory Group Authorization (Optional)

When enabled, CViche checks Enterprise Directory group membership at login via LDAP:

- **Access group:** Users must be a member of this group to access CViche. Users not in the group are denied at login.
- **Admin group:** Users in this group receive the admin role. Admin group membership does not grant access by itself -- users must also be in the access group.
- Group DNs are configured in `auth_config.yaml` under `ed.access_group` and `ed.admin_group` (e.g., `cn=App-CViche-Users,ou=Groups,dc=weill,dc=cornell,dc=edu`).
- LDAP connection requires the following environment variables on the CViche server: `ED_LDAP_URL`, `ED_LDAP_BIND_DN`, `ED_LDAP_BIND_PASSWORD`.

Group membership is re-checked on each request using a cache with a 5-minute TTL. If the Enterprise Directory is temporarily unavailable, CViche uses the last-known result for up to 30 minutes before denying access.

## Testing the Integration

After registering CViche as an SP with the WCM IdP:

1. Navigate to the CViche login page. The "Sign in with SSO" button should be visible.
2. Click the SSO button. You should be redirected to the WCM IdP login page.
3. Authenticate with your WCM credentials.
4. After authentication, you should be redirected back to CViche with an active session.
5. Verify that your name and email appear correctly in CViche (visible in the user profile area).

For local development and testing, CViche includes a mock IdP configuration (see `docker-compose.mock-idp.yml` in the repository).

## Contact

The application owner contact information is configurable via `ed.contact_name` and `ed.contact_email` in `auth_config.yaml`. This contact is shown in access denial messages when a user is not authorized to use CViche.
