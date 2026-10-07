"""Admin dashboard API: one router per domain (#334), combined into ``router``.

main.py mounts ``router`` under /api with the "admin" tag. Every route needs
the admin role except GET /admin/export/{export_type}, which staff may call
for the feedback export.

  stats    GET /admin/stats, the Overview tab's aggregates
  users    list users, change a user's role, status or limits
  runs     the runs listing, run delete, orphan reaper, rescoring, queue
           diagnostics, and deleting one feedback row (a run's child)
  config   the admin-editable SystemConfig keys: config, consent version
           publish, and session revocation (the session epoch)
  exports  the CSV downloads

Dependencies run one way: each module imports app.auth, app.database,
app.models (for the User annotation), app.schemas and app.services -- never
another module in this package (CODING_STANDARDS 1.3), and none queries the
database itself (2.1). The include order below is the route order of the
single module this replaced, so the route table and the OpenAPI document are
unchanged by the split.
"""
from fastapi import APIRouter

from app.api.admin_routes import config, exports, runs, stats, users

router = APIRouter()
router.include_router(stats.router)
router.include_router(users.router)
router.include_router(runs.router)
router.include_router(config.router)
router.include_router(exports.router)
