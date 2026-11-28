from django.urls import include, path
urlpatterns = [path("", include("care.urls"))]
handler400 = "care.views.system.bad_request"
handler403 = "care.views.system.permission_denied"
handler404 = "care.views.system.not_found"
handler500 = "care.views.system.server_error"
