from ninja import NinjaAPI

api = NinjaAPI(title="Spool API", version="1.0")

# Routers are added incrementally alongside the page/action that needs them
# (see spool-stack-addendum.md §2) — e.g. api.add_router("/history", ...)
# lands with the History page, not up front.
