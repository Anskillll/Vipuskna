from django import template


register = template.Library()

UKRAINIAN_WEEKDAYS = (
    'Понеділок',
    'Вівторок',
    'Середа',
    'Четвер',
    "П'ятниця",
    'Субота',
    'Неділя',
)


@register.filter
def uk_weekday(value):
    if not value:
        return ''
    return UKRAINIAN_WEEKDAYS[value.weekday()]
