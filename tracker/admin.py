from django.contrib import admin

from .models import (
    Episode,
    ExternalAccount,
    ExternalRating,
    Genre,
    NuvioConnection,
    Profile,
    ReleaseSchedule,
    Title,
    WatchEvent,
    WatchList,
    WatchListItem,
    WatchProgress,
)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "user", "created_at")
    search_fields = ("display_name", "user__username")


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    search_fields = ("name",)


class EpisodeInline(admin.TabularInline):
    model = Episode
    extra = 0


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    # Not linked from the sidebar UI — exists so Episode can be used as an
    # autocomplete_fields target on WatchEvent/WatchProgress/ReleaseSchedule.
    list_display = ("title", "season", "episode", "name")
    search_fields = ("name", "title__name")


class ExternalRatingInline(admin.TabularInline):
    model = ExternalRating
    extra = 0


@admin.register(Title)
class TitleAdmin(admin.ModelAdmin):
    list_display = ("name", "media_type", "year")
    list_filter = ("media_type",)
    search_fields = ("name",)
    filter_horizontal = ("genres",)
    inlines = [EpisodeInline, ExternalRatingInline]


@admin.register(WatchEvent)
class WatchEventAdmin(admin.ModelAdmin):
    list_display = ("profile", "title", "episode", "watched_at", "is_rewatch")
    list_filter = ("is_rewatch", "title__media_type")
    date_hierarchy = "watched_at"
    autocomplete_fields = ("title", "episode")


@admin.register(WatchProgress)
class WatchProgressAdmin(admin.ModelAdmin):
    list_display = ("profile", "title", "status", "current_episode", "updated_at")
    list_filter = ("status",)
    autocomplete_fields = ("title", "current_episode")


class WatchListItemInline(admin.TabularInline):
    model = WatchListItem
    extra = 0
    autocomplete_fields = ("title",)


@admin.register(WatchList)
class WatchListAdmin(admin.ModelAdmin):
    list_display = ("name", "profile", "is_shared", "created_at")
    list_filter = ("is_shared",)
    inlines = [WatchListItemInline]


@admin.register(ReleaseSchedule)
class ReleaseScheduleAdmin(admin.ModelAdmin):
    list_display = ("title", "release_type", "release_date", "episode")
    list_filter = ("release_type",)
    date_hierarchy = "release_date"
    autocomplete_fields = ("title", "episode")


@admin.register(ExternalAccount)
class ExternalAccountAdmin(admin.ModelAdmin):
    list_display = ("profile", "provider", "connected_at", "token_expires_at")
    list_filter = ("provider",)


@admin.register(NuvioConnection)
class NuvioConnectionAdmin(admin.ModelAdmin):
    # No token field here (or exposed on the change form beyond its raw
    # ciphertext, since encryption happens before it ever reaches the
    # DB) - matches ExternalAccountAdmin's own list_display choice above.
    list_display = ("profile", "email", "nuvio_profile_id", "sync_enabled", "connected_at", "last_synced_at")
