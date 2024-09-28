import pytest
from fastapi.testclient import TestClient
from src.server import app, get_db
client = TestClient(app)

@pytest.fixture
def mock_db(monkeypatch):
    class MockDB:
        def search(self, query):
            """
            Mock search method that returns course data for the specified year and term.
            """
            return [
                {
                    "year": 2024,
                    "term": "4410",
                    "details": [
                        {
                            "SUBJECT": "COMP SCI",
                            "COURSE_ID": "111459",
                            "COURSE_OFFER_NBR": 1,
                            "ACAD_CAREER": "UGRD",
                            "TERM": "4410",
                            "TERM_DESCR": "Semester 1",
                            "COURSE_TITLE": "Introduction to Programming",
                            "CAMPUS": "North Terrace",
                            "CATALOG_NBR": "1001",
                            "CLASS_NBR": 13722,
                            "UNITS": 3,
                            "AVAILABLE_FOR_NON_AWARD_STUDY": "Yes"
                        }
                    ]
                },
                {
                    "year": 2024,
                    "term": "4410",
                    "details": [
                        {
                            "SUBJECT": "ABLEINT",
                            "COURSE_ID": "107654",
                            "COURSE_OFFER_NBR": 1,
                            "ACAD_CAREER": "UGRD",
                            "TERM": "4410",
                            "TERM_DESCR": "Semester 1",
                            "COURSE_TITLE": "Crafting Careers",
                            "CAMPUS": "North Terrace",
                            "CATALOG_NBR": "1001",
                            "CLASS_NBR": 13723,
                            "UNITS": 3,
                            "AVAILABLE_FOR_NON_AWARD_STUDY": "Yes"
                        }
                    ]
                }
            ]

        def all(self):
            """
            Mock 'all' method that returns a list containing subjects data.
            """
            return [
                {
                    "subjects": [
                        {
                            "SUBJECT": "COMP SCI",
                            "DESCR": "Computer Science"
                        },
                        {
                            "SUBJECT": "ABLEINT",
                            "DESCR": "Faculty of ABLE WIL Courses"
                        }
                    ]
                }
            ]

    def override_get_db():
        return MockDB()

    app.dependency_overrides[get_db] = override_get_db
    yield MockDB()
    app.dependency_overrides.clear()

def test_get_subjects(mock_db):
    """
    Test the /subjects endpoint using the mocked database.
    """
    response = client.get("/subjects?year=2024&term=Semester%201")
    assert response.status_code == 200
    data = response.json()
    assert "subjects" in data
    assert isinstance(data["subjects"], list)

    
    subjects = data["subjects"]
    assert any(subject["code"] == "COMP SCI" and subject["name"] == "Computer Science" for subject in subjects)
    assert any(subject["code"] == "ABLEINT" and subject["name"] == "Faculty of ABLE WIL Courses" for subject in subjects)

def test_get_courses(mock_db):
    """
    Test the /courses endpoint using the mocked database.
    """
    
    response = client.get("/courses?subject=COMP SCI&year=2024&term=Semester%201")
    
    assert response.status_code == 200
    data = response.json()
    assert "courses" in data
    courses = data["courses"]
    assert isinstance(courses, list)
    assert len(courses) == 2  
    assert courses[0]["name"]["subject"] == "COMP SCI"
    assert courses[0]["name"]["code"] == "1001"
    assert courses[0]["name"]["title"] == "Introduction to Programming"