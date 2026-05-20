# CViche SAML SP Registration Guide

This document provides the information needed to register CViche as a SAML 2.0 Service Provider with the Weill Cornell Medicine Identity Provider.

## SP Metadata

CViche exposes a standard SAML 2.0 SP metadata endpoint for automated import:

- **Metadata URL:** `https://cviche.weill.cornell.edu/api/saml/metadata`
- **Entity ID:** `https://cviche.weill.cornell.edu/shibboleth`

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
| Assertion Consumer Service | `https://cviche.weill.cornell.edu/api/saml/acs` | HTTP-POST |
| Single Logout Service | `https://cviche.weill.cornell.edu/api/saml/logout` | HTTP-Redirect |
| SP Metadata | `https://cviche.weill.cornell.edu/api/saml/metadata` | HTTP-GET |

The Assertion Consumer Service (ACS) endpoint receives the signed SAML assertion from the IdP via HTTP-POST binding. The logout endpoint clears the local session only; there is no IdP Single Logout round-trip.

## Discovery Service Integration

CViche uses the WCM discovery service (WAYF) at `login.weill.cornell.edu` for IdP selection.

- The discovery service URL is configured in CViche's `auth_config.yaml` under `saml.discovery_url`.
- When a user clicks "Sign in with SSO", CViche constructs a SAML AuthnRequest via pysaml2 and redirects the user to the IdP. The discovery service URL is referenced by the frontend for informational display only -- the actual SAML redirect is handled by the backend.
- The `SIMPLESAMLPHP_SP_ENTITY_ID` registered with the discovery service must match CViche's configured entity ID (`https://cviche.weill.cornell.edu/shibboleth`).

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
- Group DNs are configured in `auth_config.yaml` under `ed.access_group` and `ed.admin_group` (e.g., `cn=ITS:Library:CViche/user-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu`).
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

## Post-Registration: Wiring IdP Values

When WCM IT returns the SAML Integration Request, the following CViche config values get populated. The IdP's metadata URL is the only value that flows from IT into the app's config; everything else in the IdP metadata XML (IdP entityID, SSO URL, signing cert) is fetched on demand by pysaml2.

| IT returns | Wires into | Notes |
|---|---|---|
| IdP metadata URL | `auth_config.yaml` → `saml.idp_metadata_url` | pysaml2 fetches this on each boot and refreshes; trust chains to the IdP signing cert flow from here |
| Attribute confirmation for `mail` | (no config change) | Confirms the assertion-attribute lookup in `saml_client.extract_user_attrs` will resolve `urn:oid:0.9.2342.19200300.100.1.3` |
| (already known) Discovery URL | `auth_config.yaml` → `saml.discovery_url` | `https://login.weill.cornell.edu/...` (WCM WAYF) |
| (already known) SP entity ID | `auth_config.yaml` → `saml.entity_id` | `https://cviche.weill.cornell.edu/shibboleth` |
| (already known) SP base URL | `auth_config.yaml` → `saml.sp_base_url` | `https://cviche.weill.cornell.edu` — the SP's reachable hostname. ACS/SLO URLs in published metadata are built as `{sp_base_url}/api/saml/{acs,logout}`. **Must** differ from `entity_id` when the entity uses Shibboleth `/shibboleth` convention; otherwise the IdP rejects assertions on Destination mismatch. |
| (already known) Cert dir | `auth_config.yaml` → `saml.cert_dir` | Where `sp.crt` and `sp.key` are mounted (e.g., `/app/web_interface/backend/certs`) |

### EKS deployment (current production)

CViche prod runs in the `cviche-dev` namespace on the `reciter` EKS cluster (us-east-1). The deployment manifests are managed outside this repo, but the patches needed are:

1. **Mount the SP keypair as a Secret volume.** Create a Secret with the pre-generated `sp.crt` + `sp.key` (from 1Password / AWS Secrets Manager — never check into git):

   ```sh
   # On a workstation with the keypair available
   kubectl -n cviche-dev create secret generic cviche-saml-sp-cert \
     --from-file=sp.crt=$HOME/cviche-saml-sp-prod.crt.pem \
     --from-file=sp.key=$HOME/cviche-saml-sp-prod.key.pem
   ```

   Patch the backend deployment to mount it read-only at the configured `cert_dir`:

   ```yaml
   spec:
     template:
       spec:
         containers:
         - name: backend
           volumeMounts:
           - name: saml-sp-cert
             mountPath: /app/web_interface/backend/certs
             readOnly: true
         volumes:
         - name: saml-sp-cert
           secret:
             secretName: cviche-saml-sp-cert
             defaultMode: 0400
   ```

2. **Provision the SAML-mode `auth_config.yaml` via ConfigMap.** Once the IdP metadata URL is in hand, render a real `auth_config.yaml` and mount it over the in-image example fallback:

   ```yaml
   auth:
     mode: saml
   saml:
     entity_id: "https://cviche.weill.cornell.edu/shibboleth"
     sp_base_url: "https://cviche.weill.cornell.edu"   # ACS/SLO endpoints in metadata are built from this; distinct from entity_id by design
     idp_metadata_url: "<value from WCM IT>"
     discovery_url: "https://login.weill.cornell.edu/..."  # WCM WAYF, confirm exact path with IT
     cert_dir: "/app/web_interface/backend/certs"
   ed:
     enabled: true
     access_group: "cn=ITS:Library:CViche/user-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu"
     admin_group:  "cn=ITS:Library:CViche/admin-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu"
     contact_name: "Paul Albert"
     contact_email: "paa2013@med.cornell.edu"
   allowed_users: []  # ED groups now govern access; allowed_users falls back if ed.enabled=false
   admin_users:  []
   rate_limits: { daily: 10, monthly: 50 }
   consent: { version: "1.0" }
   ```

   ```sh
   kubectl -n cviche-dev create configmap cviche-auth-config \
     --from-file=auth_config.yaml=/path/to/rendered-auth_config.yaml
   ```

   Mount it at `/app/web_interface/backend/auth_config.yaml` in the backend container (subPath mount to a single file).

3. **Add ED LDAP credentials as env vars** on the backend container (sourced from a new or existing Secret):

   ```yaml
   env:
   - name: ED_LDAP_URL
     valueFrom: { secretKeyRef: { name: cviche-ed-ldap, key: url } }
   - name: ED_LDAP_BIND_DN
     valueFrom: { secretKeyRef: { name: cviche-ed-ldap, key: bind_dn } }
   - name: ED_LDAP_BIND_PASSWORD
     valueFrom: { secretKeyRef: { name: cviche-ed-ldap, key: bind_password } }
   ```

4. **Roll the deployment.** `kubectl -n cviche-dev rollout restart deploy/cviche-backend`. Watch for the pod to reach `Running` (precondition: backend image is on a build that contains the latest alembic migrations — see "Image staleness" note below).

5. **Smoke test against the live IdP** (see "Testing the Integration" above).

### Image staleness note

Filing the SP registration with IT does not require a fresh image build, but flipping `auth.mode=saml` does. The image must:

- Contain the latest alembic migrations (DB and code both at head)
- Contain the `pysaml2` dependencies (already in `requirements.txt`)
- Be on a build that includes any post-cutover fixes (alembic race, non-root user, etc.)

Verify the running image tag against `git log --oneline origin/main` before flipping SAML mode.
