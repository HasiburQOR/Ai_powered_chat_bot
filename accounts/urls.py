from django.urls import include, path

from . import views

# django.contrib.auth.urls gives us, all under /accounts/:
#   login/, logout/, password_change/, password_change/done/,
#   password_reset/, password_reset/done/,
#   reset/<uidb64>/<token>/, reset/done/
urlpatterns = [
    path('after-login/', views.post_login_redirect, name='post-login-redirect'),
    path('', include('django.contrib.auth.urls')),
]
