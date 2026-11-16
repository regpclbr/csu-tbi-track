"""My profile (ASSUMPTIONS 390): /accounts/profile/, opened from the name card at the bottom of the sidebar. Always the
signed-in member's OWN account (no member ID in the URL). Thin views: the rules are accounts.services.update_own_profile
and change_own_password. Two plain forms (name + position, password); each POST says which one with form=profile|password.
"""

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from config.mail import email_sent_message

from . import services
from .permissions import staff_login_required


def _flat(error: ValidationError) -> dict[str, str]:
    if hasattr(error, "error_dict"):
        return {key: " ".join(messages_) for key, messages_ in error.message_dict.items()}
    return {"general": " ".join(error.messages)}


def _context(member, profile: dict | None = None, profile_errors: dict | None = None,
             password_errors: dict | None = None) -> dict:
    return {
        "page_title": "My profile",
        "member": member,
        "positions": services.offered_positions(member),
        "profile": profile or {"name": member.name, "position": member.position_id},
        "profile_errors": profile_errors or {},
        "password_errors": password_errors or {},
        "signatory_note": services.signatory_named_like(member),
    }


@staff_login_required
@require_http_methods(["GET", "POST"])
def profile(request: HttpRequest) -> HttpResponse:
    member = request.user
    if request.method == "GET":
        return render(request, "accounts/profile.html", _context(member))

    if request.POST.get("form") == "password":
        try:
            emailed = services.change_own_password(
                member, request.POST.get("current_password", ""), request.POST.get("new_password1", ""),
                request.POST.get("new_password2", ""),
            )
        except ValidationError as error:
            return render(request, "accounts/profile.html", _context(member, password_errors=_flat(error)), status=400)
        update_session_auth_hash(request, member)  # stay signed in
        if emailed:
            messages.success(request, email_sent_message("A confirmation email was sent to you.",
                                                         before="Your password was changed. "))
        else:
            messages.warning(request, "Your password was changed, but the confirmation email could not be sent.")
        return redirect("accounts:profile")

    raw_position = request.POST.get("position", "").strip()
    values = {"name": request.POST.get("name", ""), "position": int(raw_position) if raw_position.isdigit() else None}
    if raw_position and not raw_position.isdigit():
        return render(request, "accounts/profile.html",
                      _context(member, values, {"position": "Choose a position from the list."}), status=400)
    try:
        services.update_own_profile(member, name=values["name"], position_id=values["position"])
    except ValidationError as error:
        member.refresh_from_db()
        return render(request, "accounts/profile.html", _context(member, values, _flat(error)), status=400)
    messages.success(request, "Your profile was saved.")
    return redirect("accounts:profile")
