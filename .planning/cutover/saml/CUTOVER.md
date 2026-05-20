# CViche prod SAML cutover — apply runbook

When WCM ITS responds to RITM0791615, this is the four-command cutover.
Status today (2026-05-20): pod is on `dev-70` image, healthy, in simple
mode with placeholder allowed_users — no real user can log in. Applying
the four artifacts below fixes that.

## Prerequisites (one-time, before cutover)

- [ ] **WCM ITS response** to RITM0791615 includes:
  - IdP metadata URL → goes into `cviche-auth-config-configmap.yaml`
  - Confirmation that `mail` attribute carries the user's real email
  - WAYF discovery URL → confirm matches the value pre-filled in the ConfigMap
- [ ] **ED service-account bind** provisioned: `cn=svc-cviche,ou=ServiceAccounts,…`
  with read-only LDAPS access. Password in 1Password. File a separate ED
  ticket if not done. (Paul's personal bind is ldapmodify-only; never put
  it in pod env.)
- [ ] **Backend image** is on `dev-70.2026-05-20.15.48.22.2e2d6caf` or newer
  (carries PR #33: `saml.sp_base_url` field, `/api/saml/metadata` fix).
  Verify: `kubectl -n cviche-dev get pod -l app=cviche-backend -o jsonpath='{.items[0].spec.containers[0].image}'`

## Apply sequence

```sh
# Working dir
cd "$HOME/Dropbox/GitHub/CViche/.planning/cutover/saml"

# 1. SP signing keypair (imperative -- private key sourced from 1Password)
op item get "CViche -- SAML SP private key (prod)" --fields label="private key" --reveal > /tmp/sp.key.pem
cp ~/cviche-saml-sp-prod.crt.pem /tmp/sp.crt.pem
kubectl -n cviche-dev create secret generic cviche-saml-sp-cert \
  --from-file=sp.crt=/tmp/sp.crt.pem \
  --from-file=sp.key=/tmp/sp.key.pem \
  --dry-run=client -o yaml | kubectl apply -f -
rm /tmp/sp.key.pem
# (Keep /tmp/sp.crt.pem; it's public.)

# 2. ED LDAP bind credentials (imperative -- password sourced from 1Password)
ED_PW="$(op item get 'CViche -- ED service account bind (prod)' --fields password --reveal)"
kubectl -n cviche-dev create secret generic cviche-ed-ldap \
  --from-literal=ED_LDAP_URL='ldaps://ed.weill.cornell.edu:636' \
  --from-literal=ED_LDAP_BIND_DN='cn=svc-cviche,ou=ServiceAccounts,dc=weill,dc=cornell,dc=edu' \
  --from-literal=ED_LDAP_BIND_PASSWORD="$ED_PW" \
  --dry-run=client -o yaml | kubectl apply -f -
unset ED_PW

# 3. auth_config.yaml as ConfigMap
#    EDIT this file FIRST -- replace <FROM-ITS-RESPONSE> with the actual IdP
#    metadata URL; verify the discovery_url matches what ITS sent.
$EDITOR cviche-auth-config-configmap.yaml
kubectl apply -f cviche-auth-config-configmap.yaml

# 4. Patch the Deployment to mount the new Secret + ConfigMap and pull the
#    new envFrom. Triggers a rolling restart.
kubectl -n cviche-dev patch deploy cviche-backend \
  --patch-file cviche-backend-deployment-patch.yaml
kubectl -n cviche-dev rollout status deploy/cviche-backend
```

## Post-cutover smoke test

```sh
POD=$(kubectl -n cviche-dev get pod -l app=cviche-backend -o jsonpath='{.items[0].metadata.name}')

# Mode is now saml
kubectl -n cviche-dev exec $POD -- curl -s localhost:8000/api/auth/config
# -> {"mode":"saml","discovery_url":"https://shibboleth.weill.cornell.edu/..."}

# SP metadata advertises the right ACS URL (NOT /shibboleth/api/saml/acs)
kubectl -n cviche-dev exec $POD -- curl -s localhost:8000/api/saml/metadata \
  | grep -oE 'AssertionConsumerService[^>]*Location="[^"]*"'
# -> Location="https://cviche.weill.cornell.edu/api/saml/acs"

# ED env vars set
kubectl -n cviche-dev exec $POD -- env | grep ^ED_LDAP_ | sort
# -> ED_LDAP_BIND_DN=cn=svc-cviche,…
# -> ED_LDAP_BIND_PASSWORD=…     (don't echo)
# -> ED_LDAP_URL=ldaps://ed.weill.cornell.edu:636

# SP keypair mounted
kubectl -n cviche-dev exec $POD -- ls -la /app/web_interface/backend/certs/
# -> sp.crt
# -> sp.key

# auth_config.yaml override mounted (not the .example)
kubectl -n cviche-dev exec $POD -- head -5 /app/web_interface/backend/auth_config.yaml
# -> auth:
# ->   mode: saml
# -> saml:
# ->   entity_id: "https://cviche.weill.cornell.edu/shibboleth"

# End-to-end: visit https://cviche.weill.cornell.edu/api/saml/login in a
# browser. Should redirect to WCM IdP, prompt for credentials, return to
# the ACS, and land on the app's root path with a session cookie set.
# Then /api/auth/me returns the user's mail attribute as email.
```

## Rollback

If anything misbehaves during or after cutover, the fastest reverse is:

```sh
kubectl -n cviche-dev rollout undo deploy/cviche-backend
```

That rolls back to the prior ReplicaSet (simple mode, .example fallback,
no SAML mounts). Pod returns to the pre-cutover state in ~30 seconds.

If the rollback also fails (e.g., the prior RS is gone), edit the
ConfigMap to set `auth.mode: simple`, re-apply, and restart the deploy.
SAML routes start 404'ing; simple-mode login resumes. SAML-mode users
already in the User table stay; they just authenticate via simple again
on next login.

## Files in this directory

| File | Role |
|---|---|
| `CUTOVER.md` | This document |
| `cviche-saml-sp-cert-secret.yaml` | SP keypair Secret -- reference shape + imperative apply recipe (private key never committed) |
| `cviche-ed-ldap-secret.yaml` | ED LDAP bind Secret -- same pattern |
| `cviche-auth-config-configmap.yaml` | auth_config.yaml ConfigMap -- declarative, one `<FROM-ITS-RESPONSE>` placeholder to fill |
| `cviche-backend-deployment-patch.yaml` | Strategic-merge patch -- adds the volume mounts and ED envFrom to the running Deployment |
| `saml-its-form-prod.md` | Filed ITS form (RITM0791615), preserved for audit |
| `cviche-saml-cert-rotation-2029.ics` | Outlook calendar reminder for cert rotation in Q1 2029 |

## Tested

- `kubectl patch --dry-run=server` on cviche-backend Deployment: passes;
  preserves existing `cviche-secrets-h4dmgg4gb6` envFrom while adding
  `cviche-ed-ldap`; both volume mounts attach correctly.
- `kubectl apply --dry-run=server` on the ConfigMap: passes.
- The fixes that make this cutover safe (sp_base_url ACS derivation,
  /api/saml/metadata endpoint) were validated end-to-end via the mock IdP
  walkthrough on 2026-05-20 (PR #33, commit `677a168`).
