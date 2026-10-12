from PySide6.QtCore import QDate, QDateTime, QLocale, QTime

from filehouse.qt_models import dates

LOCALE = QLocale(QLocale.Language.English, QLocale.Country.UnitedKingdom)
TODAY = QDate(2026, 10, 11)  # a Sunday


def at(days_ago, hour=10, minute=42):
    return QDateTime(TODAY.addDays(-days_ago), QTime(hour, minute)).toSecsSinceEpoch()


def friendly(seconds):
    return dates.friendly(seconds, LOCALE, TODAY)


def test_today_has_the_time():
    assert friendly(at(0)) == "Today, 10:42"
    assert friendly(at(0, 0, 0)) == "Today, 00:00"


def test_yesterday_is_named_also_one_minute_before_midnight():
    assert friendly(at(1)) == "Yesterday"
    assert friendly(at(1, 23, 59)) == "Yesterday"


def test_the_last_week_goes_by_weekday():
    assert friendly(at(2)) == "Fri"
    assert friendly(at(6)) == "Mon"


def test_older_is_a_date_and_so_is_the_same_weekday_a_week_ago():
    assert friendly(at(7)) == LOCALE.toString(TODAY.addDays(-7), QLocale.FormatType.ShortFormat)
    assert friendly(at(400)) == "06/09/2025"


def test_a_time_in_the_future_is_a_date():
    assert friendly(at(-1)) == "12/10/2026"


def test_unknown_is_empty():
    assert friendly(0) == ""
