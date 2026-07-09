from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("movies-tv/<str:tab>/", views.library, {"media_type": "movie_tv"}, name="movies_tv"),
    path("anime/<str:tab>/", views.library, {"media_type": "anime"}, name="anime"),
    path("history/", views.history, name="history"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("lists/", views.lists, name="lists"),
    path("lists/create/", views.create_list, name="create_list"),
    path("lists/<int:list_id>/", views.list_detail, name="list_detail"),
    path("lists/<int:list_id>/delete/", views.delete_list, name="delete_list"),
    path("lists/<int:list_id>/add/", views.add_to_list, name="add_to_list"),
    path("lists/<int:list_id>/remove/", views.remove_from_list, name="remove_from_list"),
    path("lists/<int:list_id>/search-titles/", views.search_titles, name="search_titles"),
    path("stats/", views.stats, name="stats"),
    path("activity/", views.activity, name="activity"),
    path("settings/", views.settings_view, name="settings"),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="tracker/login.html"),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
]
