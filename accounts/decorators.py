"""Decorators for the staff-only areas of the site."""
from functools import wraps

from django.contrib.auth.views import redirect_to_login


def staff_required(view_func):
    """Same as Django's staff_member_required, but redirects unauthenticated
    users to the branded login page at /accounts/login/ instead of the Django
    admin login."""

    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.is_active and request.user.is_staff:
            return view_func(request, *args, **kwargs)
        return redirect_to_login(request.get_full_path(), 'login')
    return _wrapped_view
