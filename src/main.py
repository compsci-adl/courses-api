from src.api.DataFetcher import DataFetcher

#  This is now a test
if __name__ == "__main__":
    YEAR = 2024
    SUBJECT = "COMP SCI"
    COURSE_ID = 107592
    TERM = 4410

    subjects = DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/SUBJECTS_BY_YEAR/queryx&virtual=Y&year_from={YEAR}&year_to={YEAR}"
    )
    print(subjects.get()["data"])

    courses = DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_SEARCH/queryx&virtual=Y&year={YEAR}&subject={SUBJECT}&pagenbr=1&pagesize=500"
    )
    print(courses.get()["data"])

    course_detail = DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_DTL/queryx&virtual=Y&year={YEAR}&courseid={COURSE_ID}&term={TERM}"
    )
    print(course_detail.get()["data"][0])

    course_class_list = DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_CLASS_LIST/queryx&virtual=Y&crseid={COURSE_ID}&offer=1&term={TERM}&session=1"
    )
    print(course_class_list.get()["data"][0])

    terms = DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/TERMS/queryx&virtual=Y&year_from={YEAR}&year_to={YEAR}"
    )
    print(terms.get()["data"])
