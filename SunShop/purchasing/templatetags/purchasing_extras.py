from django import template

register = template.Library()


@register.filter
def person(user, fallback="SunShop"):
    """A user's full name, else username - or `fallback` if there's no user (e.g. imported history)."""
    if user is None:
        return fallback
    return user.get_full_name() or user.username
