from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path
from django.views.generic import RedirectView

admin.site.site_header = "DWG-FIND 管理"
admin.site.site_title = "DWG-FIND 管理"

urlpatterns = [
    # 管理画面のログインも一般のログイン画面にまとめる（?next=/admin/ は引き継ぐ）
    path("admin/login/", login_not_required(RedirectView.as_view(url="/accounts/login/", query_string=True))),
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="drawings/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("drawings.urls")),
]
