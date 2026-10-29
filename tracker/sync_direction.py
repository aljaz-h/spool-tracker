"""Which way a connected service may move data: into Spool (PULL), out of
Spool (PUSH), both, or neither (OFF). Chosen per connection - when it is
first connected, and any time after on its card in Settings - so connecting
a service never has to mean it starts changing a watch history.

Every connection that existed before this setting pulls, exactly as it
always did. A brand new one starts OFF and does nothing until the person
picks (views.sync_direction_setup), so the choice always comes before the
first read.

PUSH_PROVIDERS lists the services Spool can send data to. Only those offer
the PUSH and BOTH options; the rest offer pull and off. Today that is WeTrakr.
"""

from .models import SyncDirection

PULL, PUSH, BOTH, OFF = SyncDirection.PULL, SyncDirection.PUSH, SyncDirection.BOTH, SyncDirection.OFF

# Services Spool is able to send data to (watch history, so far). Add one here
# when its push exists.
PUSH_PROVIDERS = frozenset({"wetrakr"})

NAMES = {"trakt": "Trakt", "simkl": "Simkl", "wetrakr": "WeTrakr", "nuvio": "Nuvio", "tmdb": "TMDB", "mal": "MyAnimeList"}

HELP = {
    PULL: "New watches, ratings and lists come from {name} into Spool, on the schedule below. Nothing is sent back.",
    PUSH: "Spool sends its data to {name}. Nothing is read from {name}, so your Spool history is never changed by it.",
    BOTH: "Spool reads from {name} and sends to it.",
    OFF: "The account stays connected, but nothing moves in either direction until you change this.",
}


def allows_pull(account):
    return account.sync_direction in (PULL, BOTH)


def allows_push(account):
    return account.sync_direction in (PUSH, BOTH)


def supports_push(provider):
    return provider in PUSH_PROVIDERS


def choices_for(provider):
    """[(value, label, help)] for the options this service offers, with the
    safe ones first."""
    name = NAMES.get(provider, provider.title())
    values = [PULL, OFF]
    if supports_push(provider):
        values = [PULL, PUSH, BOTH, OFF]
    return [(v.value, v.label, HELP[v].format(name=name)) for v in values]


def is_valid_for(provider, value):
    return value in {v for v, _label, _help in choices_for(provider)}
