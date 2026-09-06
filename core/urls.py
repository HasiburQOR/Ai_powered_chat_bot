from django.contrib import admin
from django.urls import include, path
from core.views import index

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('webhooks/', include('webhooks.urls')),
    path('widget/', include('widget.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('', index, name='index'),
]
