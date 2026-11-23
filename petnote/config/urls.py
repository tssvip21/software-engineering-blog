from django.urls import include, path
urlpatterns = [path("", include("care.urls"))]
handler400 = "care.views.bad_request"
handler403 = "care.views.permission_denied"
handler404 = "care.views.not_found"
handler500 = "care.views.server_error"
