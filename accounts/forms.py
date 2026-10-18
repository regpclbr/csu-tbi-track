"""Plain input forms for login and password change. The rules live in services.py."""

from django import forms


class LoginForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput, strip=False)


class PasswordChangeForm(forms.Form):
    old_password = forms.CharField(label="Current password", widget=forms.PasswordInput, strip=False)
    new_password1 = forms.CharField(label="New password", widget=forms.PasswordInput, strip=False)
    new_password2 = forms.CharField(label="New password (again)", widget=forms.PasswordInput, strip=False)

    def clean(self) -> dict:
        cleaned = super().clean()
        if cleaned.get("new_password1") != cleaned.get("new_password2"):
            raise forms.ValidationError("The two new passwords do not match.")
        return cleaned
