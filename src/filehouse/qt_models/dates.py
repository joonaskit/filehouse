"""When a file was changed, the way people say it: "Today, 10:42", "Yesterday", "Mon"."""

from PySide6.QtCore import QDate, QDateTime, QLocale

# The words are the app's, and so in its language; times and dates are written the user's way
_WORDS = QLocale(QLocale.Language.English)


def friendly(seconds, locale, today=None):
    """`seconds` since the epoch as a short text. Empty for 0, which means unknown."""
    if not seconds:
        return ""
    moment = QDateTime.fromSecsSinceEpoch(seconds)
    date = moment.date()
    days_ago = date.daysTo(today or QDate.currentDate())
    short = QLocale.FormatType.ShortFormat
    if days_ago == 0:
        return f"Today, {locale.toString(moment.time(), short)}"
    if days_ago == 1:
        return "Yesterday"
    if 1 < days_ago < 7:
        # Not a week ago: that is the same weekday as today
        return _WORDS.dayName(date.dayOfWeek(), short)
    return locale.toString(date, short)
