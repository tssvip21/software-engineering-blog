from django.conf import settings
from django.urls import path
from . import auth, views

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("auth/google/start/", auth.google_start, name="google_start"),
    path("auth/google/callback/", auth.google_callback, name="google_callback"),
    path("auth/logout/", auth.logout_view, name="logout"),
    path("join/", views.join_entry, name="join"),
    path("join/preview/", views.join_preview, name="join_preview"),
    path("join/confirm/", views.join_confirm, name="join_confirm"),
    path("rooms/new/", views.room_new, name="room_new"),
    path("rooms/<uuid:room_id>/", views.today, name="today"),
    path("rooms/<uuid:room_id>/state/", views.room_state, name="room_state"),
    path("rooms/<uuid:room_id>/modules/", views.modules, name="modules"),
    path("rooms/<uuid:room_id>/modules/settings/", views.module_settings, name="module_settings"),
    path("rooms/<uuid:room_id>/modules/<slug:module>/", views.module_page, name="module"),
    path("rooms/<uuid:room_id>/modules/<slug:module>/new/", views.record_new, name="record_new"),
    path("rooms/<uuid:room_id>/modules/<slug:module>/statistics/", views.statistics, name="statistics"),
    path("rooms/<uuid:room_id>/records/", views.records, name="records"),
    path("rooms/<uuid:room_id>/records/<uuid:activity_id>/", views.record_detail, name="record_detail"),
    path("rooms/<uuid:room_id>/records/<uuid:activity_id>/edit/", views.record_edit, name="record_edit"),
    path("rooms/<uuid:room_id>/records/<uuid:activity_id>/cancel/", views.record_cancel, name="record_cancel"),
    path("rooms/<uuid:room_id>/overview/", views.overview_page, name="overview"),
    path("rooms/<uuid:room_id>/products/", views.products, name="products"),
    path("rooms/<uuid:room_id>/products/new/", views.product_edit, name="product_new"),
    path("rooms/<uuid:room_id>/products/<uuid:product_id>/", views.product_edit, name="product_edit"),
    path("rooms/<uuid:room_id>/settings/", views.room_settings, name="room_settings"),
    path("rooms/<uuid:room_id>/delete/", views.room_delete, name="room_delete"),
    path("rooms/<uuid:room_id>/archive/", views.room_archive, name="room_archive"),
    path("rooms/<uuid:room_id>/leave/", views.room_leave, name="room_leave"),
    path("rooms/<uuid:room_id>/members/", views.members, name="members"),
    path("rooms/<uuid:room_id>/members/<uuid:member_id>/change/", views.member_change, name="member_change"),
    path("rooms/<uuid:room_id>/invitations/new/", views.invitation_create, name="invitation_create"),
    path("rooms/<uuid:room_id>/invitations/<uuid:invite_id>/revoke/", views.invitation_revoke, name="invitation_revoke"),
    path("rooms/<uuid:room_id>/routines/", views.routines, name="routines"),
    path("rooms/<uuid:room_id>/routines/new/", views.routine_edit, name="routine_new"),
    path("rooms/<uuid:room_id>/routines/<uuid:routine_id>/edit/", views.routine_edit, name="routine_edit"),
    path("rooms/<uuid:room_id>/routines/items/<uuid:item_id>/link/", views.routine_link, name="routine_link"),
]
if settings.DEMO_MODE and settings.DEBUG:
    urlpatterns += [path("demo/login/", auth.demo_login, name="demo_login")]
