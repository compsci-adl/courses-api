"""Tests for scraper.py's unit-value parsing.

Regression test for the "units" scraping bug: `process_course` looked
up `course_details["unit_value"]`, but `data_parser.get_course_details`
returns the field under the key "units", so the lookup always fell
back to the default of 6. Additionally, the raw scraped value could
contain non-digit text (e.g. "3 units"), which crashed a bare
`int(...)` conversion.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scraper import parse_units  # noqa: E402


def test_parse_units_plain_digits():
    assert parse_units("12") == 12


def test_parse_units_with_trailing_text():
    """Scraped text like "3 units" should still parse to 3, not crash."""
    assert parse_units("3 units") == 3


def test_parse_units_missing_falls_back_to_default():
    assert parse_units(None) == 6


def test_parse_units_unparseable_falls_back_to_default():
    assert parse_units("N/A") == 6


def test_parse_units_custom_default():
    assert parse_units(None, default=0) == 0
