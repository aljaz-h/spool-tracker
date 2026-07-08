def active_profile(request):
    """Exposes the signed-in user's Profile and household size to every template.

    Placeholder until the Profile model + auth wiring lands (build step 3/4) —
    returns empty values so templates can reference `active_profile` /
    `show_activity_nav` safely from the very first page render.
    """
    return {
        "active_profile": None,
        "show_activity_nav": False,
    }
