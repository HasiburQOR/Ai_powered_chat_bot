from django.shortcuts import redirect


def index(request):
    """Site root: send visitors to the dashboard.

    Unauthenticated users are bounced to the branded login page by the
    staff_required decorator on the dashboard home view.
    """
    return redirect("dashboard-home")

