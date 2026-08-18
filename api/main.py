from ninja import NinjaAPI

from api.routers.history import router as history_router
from api.routers.reports import router as reports_router
from api.routers.scrobble import router as scrobble_router
from api.routers.watch_progress import router as watch_progress_router

api = NinjaAPI(title="Spool API", version="1.0")

# Routers are added incrementally alongside the page/action that needs them
# (see spool-stack-addendum.md §2), not all up front.
api.add_router("/history", history_router)
api.add_router("/watch-progress", watch_progress_router)
# Neither of these two is authed via the browser's own session cookie
# (django_auth, above) - see api/auth.py, docs/SCROBBLE_API.md, and
# spool-wrapped's contract.md. Separate credential types/auth classes on
# purpose (ServiceAPIKey vs. ApiToken) - one must never authenticate the
# other's calls.
api.add_router("/scrobble", scrobble_router)
api.add_router("/reports", reports_router)
