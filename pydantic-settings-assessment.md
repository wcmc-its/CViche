# Should CViche move its configuration to pydantic-settings?

Prepared for Mahender (mrj4001). This assessment is based on `dev` at commit `2cae24d`. No code was changed.

## Recommendation: don't adopt pydantic-settings now

I'd hold off. The scattered `os.getenv()` calls are a real problem, and the project has already written it down. But they're the smaller part of the configuration code, and pydantic-settings would mostly reimplement a layered resolver this project already has.

## What the codebase has today

**Direct env reads (production code, tests excluded):** about 33 ad hoc `os.getenv`/`os.environ` reads in 17 files, covering about 26 distinct variable names. The heaviest files are `web_interface/backend/app/main.py` (5), `auth.py`, `database_factory.py` and `logging_config.py`. In the pipeline, they are stage 5, `prompt_logger`/`prompt_analyzer`, `stage_3b` and `llm/bedrock.py`.

**The dominant pattern is not `os.getenv`.** It's a layered resolver you should reuse:

- **Backend:** `get_config(section, KEY, default)` in `web_interface/backend/app/config_loader.py:124`. It checks the env var first, then that key in `auth_config.yaml` (mounted from the k8s ConfigMap), then the default. It has **69 call sites** and about **39 distinct keys** (`CVICHE_REDIS_URL`, `CVICHE_SESSION_TTL`, `DB_HOST`, `ED_LDAP_URL`, …). A database-backed `SystemConfig` layer sits on top for admin-managed keys.
- **Pipeline:** `get_llm_env_config()` in `src/unified_pipeline/config.py:567` mirrors `get_config` without importing the web app. This split is deliberate (#267): the pipeline must not depend on the web package.
- `pydantic` 2.13 is already a dependency, and `SecretStr` is already used for the LDAP bind password. `pydantic-settings` itself is **not** installed.

Overall there are roughly 65–70 distinct settings.

**The project's own rule speaks to this.** `docs/CODING_STANDARDS.md` §7.2 says: *"Do not add a mechanism; use the one that exists, or delete one first."* It lists nine configuration mechanisms today, aims for two (one for the backend, one for the pipeline), and names "scattered direct `os.environ.get()` reads" as mechanism #9. Unless pydantic-settings fully replaces `get_config` and `get_llm_env_config`, it becomes a tenth mechanism. The standards also say the default answer for a new dependency is no.

## What pydantic-settings would give you

- Typed parsing and validation in one place. Today integer parsing and fallback is written out by hand in many places (`config_service.py`, `concurrency.py`, `llm/retry.py`).
- One default per key. Some keys are read in several places today: `ED_LDAP_BIND_PASSWORD` ×3, `CVICHE_STORAGE_BACKEND` ×3, `CVICHE_REDIS_URL` ×3.
- `SecretStr` masking for `CVICHE_SESSION_SECRET` and `DB_PASSWORD`.
- A single list of every setting.

These benefits are real but modest at this size.

## Risks and behavior changes

1. **The YAML fallback isn't built in.** pydantic-settings has no "this key in this YAML section" fallback, so you'd write a custom settings source, which is essentially `get_config` again.
2. **Empty strings mean "unset" today.** `get_config` uses `if value:`, so `CVICHE_ENABLE_DOCS=""` falls through to the YAML. pydantic-settings treats an empty value as set unless you pass `env_ignore_empty=True`, and an empty integer field fails validation.
3. **Booleans are parsed differently at each site.** Some check `== "1"` (`CVICHE_INIT_DB`, `CVICHE_ALLOW_SIMPLE_AUTH`), some `.lower() == "true"` (`CVICHE_SECURE_COOKIES`), some accept `{"1","true","yes","on"}` (email intake). For example, `CVICHE_INIT_DB=true` currently means **off**; as a pydantic `bool` it would mean on.
4. **Bad values fail differently.** Many integer knobs quietly fall back to their default on a bad value; pydantic would refuse to start. Some code is deliberately fail-closed (`DB_AUTH_MODE` raises; the session-secret check depends on `ENVIRONMENT`) and must stay that way.
5. **Fallback chains.** Stage 5 reads `NCBI_API_KEY` and falls back to `PUBMED_API_KEY`, while `bulk_pubmed_fetcher.py` reads only `PUBMED_API_KEY`. Merging them is a behavior change. S3 storage similarly falls back from `AWS_REGION` to `AWS_DEFAULT_REGION`.
6. **Secrets must stay env-only.** Routing `CVICHE_SESSION_SECRET`, `DB_PASSWORD` or `ED_LDAP_BIND_PASSWORD` through a YAML-backed source would let a secret live in the ConfigMap file, which breaks §7.5.
7. **When values are read.** `get_config` re-reads the YAML on every call, and many sites read at call time rather than at import. A cached `Settings` object changes that timing. It matters little in production (the ConfigMap is mounted with `subPath`, so it never updates live) but a lot in tests.
8. **Tests.** There are 413 `monkeypatch.setenv`/`delenv`/`patch.dict(os.environ)` lines across 43 test files, plus 37 `reload()` calls. A cached singleton would need cache-clearing fixtures throughout.
9. **No `.env` loading in Python today.** Docker Compose injects the variables. Turning on `env_file` would quietly change local behavior.
10. **Some reads should stay raw.** `pdf_sandbox._child_env()` passes a filtered `os.environ` to a child process, and the worker reads `HOSTNAME`.

## How much would change

A full migration touches about 17 files of direct reads, about 15 files of `get_config` callers (69 sites), the pipeline resolver, both `requirements.txt` files, and a large share of those 43 test files. The line count is modest. The review and verification work is large: CLAUDE.md warns that a clean CLI or corpus-batch run doesn't prove the web path works, so both drivers need checking.

## What I'd do instead

Do what §7.2 already asks for:

- Move the roughly 26 stray direct reads into the two existing resolvers.
- Add small typed helpers (`get_config_int`, `get_config_bool`) with one documented parsing rule, so per-site parsing stops drifting.
- Keep secrets, `HOSTNAME`, the sandbox passthrough and the standard AWS SDK variables as deliberate direct reads.

This removes mechanism #9 without adding a dependency.

## If you do it later anyway

pydantic-settings becomes worth it only when it **replaces** `get_config` and `get_llm_env_config` outright, so the mechanism count goes down. To keep the current names and behavior:

1. **Inventory every key and pin current behavior.** For each key record its name, YAML section, type, default, parsing rule, whether it's read at import or call time, and whether it's a secret. Write tests that pin today's behavior for empty strings, bad integers and boolean spellings before changing anything.
2. **Backend `Settings(BaseSettings)`:**
   - Use the exact env var names as field names (`case_sensitive=True`, no prefix) and `env_ignore_empty=True`.
   - Override `settings_customise_sources` to read env first, then the YAML section, then defaults.
   - Express fallback chains with `AliasChoices`.
   - Make secrets `SecretStr` and keep them out of the YAML source.
   - Leave `env_file` off.
   - Add lenient validators where the code falls back to the default today, and write down every place you switch to failing fast.
3. **Separate `PipelineSettings` in `unified_pipeline`.** It must not import the web app.
4. **Migrate gradually.** Turn `get_config(section, key, default)` into a thin shim over the settings object so the 69 call sites can move a few at a time. Use a cached `get_settings()` plus a test fixture that clears the cache.
5. **Finish by deleting the old mechanisms.** Remove `get_config` and `get_llm_env_config`, and update the §7.2 inventory.
6. **Validate.** Run the full test suite, a corpus batch through the CLI, a run through the web orchestrator, and a deploy to the dev overlay.
