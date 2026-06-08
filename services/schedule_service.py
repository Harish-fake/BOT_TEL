import re
from typing import Optional


CRON_PATTERNS: dict[str, str] = {
    "daily_midnight": "0 0 * * *",
    "daily_630am": "30 6 * * *",
    "daily_930am": "30 9 * * *",
    "daily_12pm": "0 12 * * *",
    "daily_630pm": "30 18 * * *",
    "daily_930pm": "30 21 * * *",
    "weekly_monday": "30 9 * * 1",
    "weekly_wednesday": "30 9 * * 3",
    "weekly_friday": "30 9 * * 5",
    "hourly": "0 * * * *",
}


class ScheduleService:

    @staticmethod
    def human_to_cron(human_readable: str) -> Optional[str]:
        hr = human_readable.strip().lower()

        if hr in CRON_PATTERNS:
            return CRON_PATTERNS[hr]

        time_match = re.match(r"(\d{1,2}):?(\d{2})?\s*(am|pm)?", hr)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or "0")
            period = time_match.group(3)

            if period == "pm" and hour != 12:
                hour += 12
            elif period == "am" and hour == 12:
                hour = 0

            if "daily" in hr or "every day" in hr:
                return f"{minute} {hour} * * *"
            if "weekday" in hr or "week day" in hr:
                return f"{minute} {hour} * * 1-5"
            if "weekend" in hr:
                return f"{minute} {hour} * * 6,7"
            if "monday" in hr:
                return f"{minute} {hour} * * 1"
            if "tuesday" in hr:
                return f"{minute} {hour} * * 2"
            if "wednesday" in hr:
                return f"{minute} {hour} * * 3"
            if "thursday" in hr:
                return f"{minute} {hour} * * 4"
            if "friday" in hr:
                return f"{minute} {hour} * * 5"
            if "saturday" in hr:
                return f"{minute} {hour} * * 6"
            if "sunday" in hr:
                return f"{minute} {hour} * * 0"
            if "weekly" in hr or "every week" in hr:
                return f"{minute} {hour} * * 1"

        custom = re.match(r"cron\s*\((.+)\)", hr)
        if custom:
            cron = custom.group(1).strip()
            if re.match(r"^(\S+\s+){4}\S+$", cron):
                return cron

        return None

    @staticmethod
    def describe_cron(expr: str) -> str:
        if expr.startswith("interval:"):
            hours = expr.split(":")[1]
            return f"Every {hours} hours (from connection time)"
        descriptions: dict[str, str] = {
            "30 6 * * *": "Daily at 6:30 AM IST",
            "30 9 * * *": "Daily at 9:30 AM IST",
            "0 12 * * *": "Daily at 12:00 PM IST",
            "30 18 * * *": "Daily at 6:30 PM IST",
            "30 21 * * *": "Daily at 9:30 PM IST",
            "30 9 * * 1": "Every Monday at 9:30 AM IST",
            "30 9 * * 3": "Every Wednesday at 9:30 AM IST",
            "30 9 * * 5": "Every Friday at 9:30 AM IST",
        }
        return descriptions.get(expr, f"Cron: `{expr}` (IST)")
