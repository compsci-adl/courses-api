import data_fetcher


def get_subjects(year: int):
    subjects = data_fetcher.DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/SUBJECTS_BY_YEAR/queryx&virtual=Y&year_from={year}&year_to={year}"
    )
    res = subjects.get()

    data = []
    if res == {}:
        return data

    for sub in res:
        data.append({"code": sub["SUBJECT"], "name": sub["DESCR"].split(" - ")[1]})

    return data


# TODO: add other functions for parsing data
