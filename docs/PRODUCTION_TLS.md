# Production TLS Termination

CViche's backend container does not terminate TLS. It is designed to run behind a TLS-terminating load balancer (AWS ALB with an ACM certificate is the supported pattern at WCM). This doc captures the contract that the container assumes so the production deploy is correct.

## What the container does

- Listens on `:8000` (uvicorn) and exposes it as `:80` through the frontend nginx pod. **Plain HTTP only inside the cluster.**
- Emits `Strict-Transport-Security: max-age=31536000; includeSubDomains` on every response (`app/main.py` `SecurityHeadersMiddleware`). Browsers will refuse `http://` to the canonical hostname for one year after the first visit.
- Sets `CVICHE_SECURE_COOKIES=true` in prod compose. Session cookies are marked `Secure`; browsers will drop them over plain HTTP.
- Honors `X-Forwarded-Proto`, `X-Forwarded-For`, and `X-Forwarded-Host` via uvicorn's `--proxy-headers --forwarded-allow-ips=*` (set in `web_interface/backend/docker-entrypoint.sh`). `request.url.scheme` therefore reflects the upstream scheme, and code that builds absolute URLs (SAML metadata, OIDC redirects) returns `https://` URLs.

## What the LB MUST do

1. **Terminate TLS** with a certificate covering the canonical hostname (e.g., `cviche.med.cornell.edu`). ACM-issued, attached to the ALB listener on `:443`.
2. **Set `X-Forwarded-Proto: https`** on every request it forwards. ALB does this automatically; verify in the target-group config that the listener uses HTTPS.
3. **Set `X-Forwarded-For`** with the original client IP (also automatic on ALB).
4. **Redirect `:80` → `:443`** at the listener level, OR drop port 80 entirely. Do not forward plain HTTP to the container; HSTS makes the recovery path painful if you ever need to.
5. **Be the only network path to the container.** With `--forwarded-allow-ips=*` uvicorn trusts every forwarded header it sees. If an arbitrary client can reach `:8000` directly (e.g., a misconfigured NodePort or a host-networked container), it could spoof `X-Forwarded-Proto: https` and elicit responses that assume TLS. In EKS the security group on the pod ENI must restrict ingress to the ALB SG only.

## What to verify before go-live

- [ ] HTTPS request to the canonical hostname returns a 200 from `/livez` (or the legacy `/health`).
- [ ] HTTP request to the same hostname redirects to HTTPS (LB-side, not application-side).
- [ ] `curl -I https://<host>/livez` shows the response `Strict-Transport-Security` header.
- [ ] `curl -I https://<host>/livez` shows the request was forwarded with `X-Forwarded-Proto: https` (check the ALB access logs).
- [ ] After a SAML round-trip through `/api/saml/login` and `/api/saml/acs`, the SP metadata served at `/api/saml/metadata` includes only `https://` URLs. (If you see `http://` here, `--proxy-headers` is not taking effect — most likely the container is being run by something other than `docker-entrypoint.sh`.)
- [ ] In the browser, the session cookie is set and survives a page reload. (If login appears to succeed but you end up logged out, `CVICHE_SECURE_COOKIES=true` is set and the response was over plain HTTP — the cookie was dropped silently.)

## Alternative: TLS terminated at the frontend nginx

If for some reason the container has to terminate TLS itself (single-host VM deploy, on-prem, no LB), add a `:443` server block to `web_interface/frontend/nginx.conf` that mounts a cert/key from `/etc/letsencrypt/live/<host>/` (or wherever provisioning lands them) and update the prod compose to expose `443:443`. The backend container still trusts `X-Forwarded-Proto` from nginx, so no app-level change is needed.

This is NOT the supported model — keep it for emergency parity only.

## Disabling secure cookies for HTTP-only environments

For a local-without-https environment (mostly for parity testing), set `CVICHE_SECURE_COOKIES=false` in the env. Do NOT do this in production — the `Secure` flag is the only thing keeping a session cookie from being exfiltrated over an accidentally non-TLS path.
