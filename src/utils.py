from datetime import datetime
import re
from typing import Union

def current_year() -> int:
    """Gets the current year."""
    return datetime.now().year

def current_sem() -> str:
    """Gets the current semester."""
    return "Semester 1" if datetime.now().month <= 6 else "Semester 2"

def get_term_number(year: int, term: str, Course, db) -> int:
    """Gets the term number from the local database."""

    # Convert aliases
    term = convert_term_alias(term)

    course_details_results = db.search(Course.year == year)

    if not course_details_results:
        raise Exception(f"No courses found for year: {year}")

    for course_details in course_details_results:
        details = course_details.get("details", [])

        for detail in details:
            if detail.get("TERM_DESCR") == term:
                return detail.get("TERM", None)

    raise Exception(f"Invalid term: {term} for year: {year}")


def meeting_date_convert(raw_date: str) -> dict[str]:
    """Converts the date format given in the meetings to "MM-DD"
    Args:
        raw_date (str): The given meeting date in the format of "DD {3-char weekday}
        - DD {3-char weekday}"
    Returns:
        formatted_date (dict[str]): The formatted meeting date in the format of "MM-DD"
    """
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    start, end = raw_date.split(" - ")

    start_d, start_m = start.split()
    start_m = str(months.index(start_m) + 1).zfill(2)

    end_d, end_m = end.split()
    end_m = str(months.index(end_m) + 1).zfill(2)

    formatted_date = {
        "start": f"{start_m}-{start_d.zfill(2)}",
        "end": f"{end_m}-{end_d.zfill(2)}"
    }
    return formatted_date


def meeting_time_convert(raw_time: str) -> str:
    """Converts the time given in meetings to "HH:mm"
    Args:
        raw_time (str): The given meeting time in the format of "H{am/pm}"
    Returns:
        formatted_time (str): The formatted meeting time in the format of "HH:mm"
    """
    period = raw_time[-2:]
    hour = int(raw_time.replace(period, "").strip())

    if period.lower() == "pm" and hour != 12:
        hour += 12
    elif period.lower() == "am" and hour == 12:
        hour = 0

    formatted_time = f"{str(hour).zfill(2)}:00"
    return formatted_time


def parse_requisites(raw_requisites: str) -> Union[list[str], None]:
    """Takes in a string of -requisites and returns a list of the parsed-out subjects
    Args:
        raw_requisites (str): The raw string containing a list of -requisites, usually
        in the format of "COMP SCI 1103, COMP SCI 2202, COMP SCI 2202B" as an example
    Returns:
        parsed_requisites (Union[list[str], None]): A list of the parsed -requisites,
        or None if raw_requisites is None
    """

    if not raw_requisites:
        return None

    # Regex pattern to match subjects and course numbers
    pattern = r'\b([A-Z]+(?:\s+[A-Z]+)*)\s+(\d{4}\w*)\b'
    matched_subjects = [" ".join(match)
                        for match in re.findall(pattern, raw_requisites)]

    return matched_subjects if matched_subjects else None


def convert_term_alias(term_alias: str) -> str:
    """Takes in a term alias and returns the CoursePlanner API name for said term
    Args:
        term_alias (str): The unconverted term, this doesn't have to be an alias
        in which case no conversion will be done
    Returns:
        str: The converted or original term depending on if a conversion was made
    """

    terms_without_digits = ("summer", "winter")
    aliases = {
        "sem": "Semester",
        "elc": "ELC Term",
        "tri": "Trimester",
        "term": "Term",
        "ol": "Online Teaching Period",
        "melb": "Melb Teaching Period",
        "pce": "PCE Term",
        "summer": "Summer School",
        "winter": "Winter School"
    }

    # Convert the alias, append its digit to the end if the term needs a digit at the end
    converted_alias = aliases.get(
        term_alias[:-1] if term_alias[-1].isdigit() else term_alias, term_alias)
    if term_alias not in terms_without_digits and term_alias[-1].isdigit() and converted_alias != term_alias:
        converted_alias += " " + term_alias[-1]

    return converted_alias