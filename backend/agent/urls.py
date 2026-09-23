from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health),
    path("options", views.options),
    path("samples", views.samples),
    path("runs", views.runs),
    path("runs/<uuid:run_id>", views.run_detail),
    path("runs/<uuid:run_id>/approve", views.approve),
]
