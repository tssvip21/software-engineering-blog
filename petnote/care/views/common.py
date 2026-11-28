"""Shared HTML errors and form error presentation."""

from django.shortcuts import render


def error(request, title, detail, status=400):
    return render(request, "care/error.html", {"title": title, "detail": detail}, status=status)


def add_form_error(form, exc):
    for message in exc.messages:
        form.add_error(None, message)
