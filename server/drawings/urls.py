from django.urls import path

from . import api, views

urlpatterns = [
    path("", views.index, name="index"),
    path("d/<int:pk>/", views.detail, name="detail"),
    path("d/<int:pk>/thumb.png", views.thumbnail, name="thumbnail"),
    path("d/<int:pk>/download", views.download, name="download"),
    path("d/<int:pk>/bom.xlsx", views.bom_xlsx, name="bom_xlsx"),
    path("export/bom.xlsx", views.bom_export, name="bom_export"),
    path("api/internal/health", api.health),
    path("api/internal/scan", api.scan),
    path("api/internal/scan/finish", api.scan_finish),
    path("api/internal/jobs/claim", api.claim),
    path("api/internal/jobs/<int:job_id>/result", api.result),
    path("api/internal/jobs/<int:job_id>/fail", api.fail),
]
