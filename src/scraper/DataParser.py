class CourseDataParser:
    """
    Parse course data retrieved from the API.
    """

    def parse_course(self, course_data: dict) -> dict:
        """
        Parse the course data to extract useful information.
        """
        parsed_data = {
            "course_id": course_data.get("course_id"),
            "title": course_data.get("title"),
            "description": course_data.get("description"),
            "credits": course_data.get("credits"),
        }
        return parsed_data

    def parse_all_courses(self, courses: list[dict]) -> list[dict]:
        """
        Parse a list of courses.
        """
        return [self.parse_course(course) for course in courses]
