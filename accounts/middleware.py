"""Force a first-sign-in password change."""

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from config.json import JsonResponse, is_api_path

from .permissions import htmx_redirect, is_htmx, is_staff_member

CHANGE_PASSWORD_FIRST = "Change your password first (first sign-in)."



class MustChangePasswordMiddleware:
    """While a member's must_change_password is True, every page except the
    change-password page and logout redirects to the change-password page.

    This covers every URL, including the Django admin and the JSON API.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = request.user
        if is_staff_member(user) and user.must_change_password and not self._is_allowed(request.path):
            if is_api_path(request.path):  # the API answers in JSON instead of redirecting
                return JsonResponse({"error": CHANGE_PASSWORD_FIRST}, status=403)
            if is_htmx(request):  # load the change-password page as a full page, not inside a partial
                return htmx_redirect(request, reverse("accounts:password_change"))
            return redirect("accounts:password_change")
        return self.get_response(request)

    @staticmethod
    def _is_allowed(path: str) -> bool:
        allowed = {reverse("accounts:password_change"), reverse("accounts:logout")}
        # Static files (CSS/JS) are allowed; document downloads are not. (Django turns the relative
        # setting "static/" into "/static/" when it is read, so this matches request paths.)
        return path in allowed or path.startswith(settings.STATIC_URL)
