import os
from datetime import datetime
from typing import Optional

import httpx

from log import logger


class YearDiscoveryService:
    """Service to discover available academic years and determine the active year."""

    FALLBACK_YEAR_SPAN = 3  # [current, current+1, current+2]
    DEFAULT_PROBE_COURSE_CODES = [
        "acct-1001",
        "comm-1001",
        "maths-1004",
        "eng-1001",
        "comp-sci-1102",
    ]

    @classmethod
    def get_fallback_years(cls, base_year: Optional[int] = None) -> list[int]:
        """Return fallback years covering 2026 through at least 2028 (or current_year + span)."""
        current_y = datetime.now().year
        start_year = min(2026, base_year or current_y)
        end_year = max(2028, (base_year or current_y) + cls.FALLBACK_YEAR_SPAN - 1)
        return list(range(start_year, end_year + 1))

    @classmethod
    def discover_available_years(
        cls, client: Optional[httpx.Client] = None
    ) -> list[int]:
        """Query the university search API / course planner to discover available academic years.

        Guarantees that target academic years (2026-2028) are always included,
        falling back gracefully if discovery fails.
        """
        search_url = (
            "https://uosa-search.funnelback.squiz.cloud/s/search.html"
            "?f.Tabs|type=Degrees+%26+Courses&form=json&num_ranks=1&profile=site-search"
            "&query=&collection=uosa~sp-aem-prod&f.Study+type|studyType=Course&start_rank=1"
        )
        should_close = False
        if client is None:
            client = httpx.Client(timeout=10.0)
            should_close = True

        discovered_years: set[int] = set()

        try:
            resp = client.get(search_url)
            if resp.status_code == 200:
                data = resp.json()
                facets = data.get("facets", [])
                for facet in facets:
                    name = str(facet.get("name", "")).lower()
                    if "year" in name:
                        for val in facet.get("allValues", []):
                            raw_val = str(val.get("data", "")).strip()
                            if raw_val.isdigit() and len(raw_val) == 4:
                                discovered_years.add(int(raw_val))
        except Exception as e:
            logger.warning(f"Failed to discover years from university API: {e}")
        finally:
            if should_close:
                client.close()

        if discovered_years:
            years = sorted(list(discovered_years))
            for y in [2026, 2027, 2028]:
                if y not in years:
                    years.append(y)
            return sorted(years)

        fallback = cls.get_fallback_years()
        logger.info(f"Using fallback academic years: {fallback}")
        return fallback

    @classmethod
    async def determine_active_years(
        cls,
        client: httpx.AsyncClient,
        available_years: list[int],
        sample_course_codes: Optional[list[str]] = None,
        active_year_override: Optional[int] = None,
    ) -> set[int]:
        """Determines all academic years that have published class timetables.

        Allows concurrent active academic years (e.g. current year and next year)
        to have their live class availability harvested.
        """
        if active_year_override is not None:
            return {int(active_year_override)}

        env_year = os.environ.get("ACTIVE_YEAR") or os.environ.get("YEAR")
        if env_year and str(env_year).strip().isdigit():
            return {int(str(env_year).strip())}

        active_years: set[int] = set()
        codes_to_probe = sample_course_codes or cls.DEFAULT_PROBE_COURSE_CODES

        for year in available_years:
            for code in codes_to_probe:
                endpoint = f"https://adelaide.edu.au/study/courses/{year}/{code}/"
                try:
                    resp = await client.get(endpoint, follow_redirects=True)
                    if resp.status_code == 200:
                        text = resp.text
                        has_classes = (
                            ("cmp-course-accordion" in text and "class" in text.lower())
                            or "class-table" in text
                            or "Enrolment class:" in text
                        )
                        if has_classes:
                            logger.info(
                                f"Active year confirmed: {year} (classes published via course {code})"
                            )
                            active_years.add(year)
                            break
                except Exception as e:
                    logger.debug(f"Error probing year {year} course {code}: {e}")

        if not active_years:
            primary = await cls.determine_active_year(
                client, available_years, sample_course_codes, active_year_override
            )
            active_years.add(primary)

        return active_years

    @classmethod
    async def determine_active_year(
        cls,
        client: httpx.AsyncClient,
        available_years: list[int],
        sample_course_codes: Optional[list[str]] = None,
        active_year_override: Optional[int] = None,
    ) -> int:
        """Determines the active academic year (with published class timetables).

        Priority:
        1. Explicit active_year_override parameter.
        2. Environment variables ACTIVE_YEAR or YEAR (from env or .env).
        3. Web probing against available years in descending order.
        4. Fallback to (current_year + 1) or current_year.
        """
        # Explicit override
        if active_year_override is not None:
            logger.info(
                f"Active academic year explicitly set to: {active_year_override}"
            )
            return int(active_year_override)

        # Check environment variables
        env_year = os.environ.get("ACTIVE_YEAR") or os.environ.get("YEAR")
        if env_year and str(env_year).strip().isdigit():
            active_year = int(str(env_year).strip())
            logger.info(
                f"Active academic year configured via environment/config: {active_year}"
            )
            return active_year

        # Probe endpoint for classes across available years descending
        codes_to_probe = sample_course_codes or cls.DEFAULT_PROBE_COURSE_CODES
        sorted_desc = sorted(available_years, reverse=True)

        for year in sorted_desc:
            for code in codes_to_probe:
                endpoint = f"https://adelaide.edu.au/study/courses/{year}/{code}/"
                try:
                    resp = await client.get(endpoint, follow_redirects=True)
                    if resp.status_code == 200:
                        text = resp.text
                        has_classes = (
                            ("cmp-course-accordion" in text and "class" in text.lower())
                            or "class-table" in text
                            or "Enrolment class:" in text
                        )
                        if has_classes:
                            logger.info(
                                f"Active year probed successfully: {year} (via course {code})"
                            )
                            return year
                except Exception as e:
                    logger.debug(f"Error probing year {year} course {code}: {e}")

        # Fallback
        current_y = datetime.now().year
        if (current_y + 1) in available_years:
            return current_y + 1
        if current_y in available_years:
            return current_y
        return sorted(available_years)[-1] if available_years else current_y
