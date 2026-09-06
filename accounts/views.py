from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect


def post_login_redirect(request):
    """Route users after login: staff go to the dashboard; anyone else is
    logged back out with a clear message (this is an internal staff tool)."""
    if request.user.is_active and request.user.is_staff:
        return redirect('dashboard-home')
    logout(request)
    messages.error(
        request,
        "This account does not have staff access. Contact an administrator if you believe this is a mistake.",
    )
    return redirect('login')
