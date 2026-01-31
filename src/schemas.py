from typing import List, Literal, Optional

from pydantic import BaseModel, Field, root_validator


class NameSchema(BaseModel):
    subject: str
    code: str
    title: str


DateField = Field(pattern=r"\d{2}-\d{2}")
TimeField = Field(pattern=r"\d{2}:\d{2}")


class DateRageSchema(BaseModel):
    start: str = DateField
    end: str = DateField


class TimeRageSchema(BaseModel):
    start: str = TimeField
    end: str = TimeField


class MeetingSchema(BaseModel):
    day: Literal[
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    location: str
    date: DateRageSchema
    time: TimeRageSchema


class ClassSchema(BaseModel):
    number: str
    section: str  # Return class section
    available_seats: str
    meetings: List[MeetingSchema]


class ClassTypeSchema(BaseModel):
    id: str
    category: Optional[Literal["enrolment", "related", "unknown"]] = "unknown"
    type: Optional[str] = None
    component: Optional[str] = None
    classes: List[ClassSchema]

    @root_validator(pre=True)
    def ensure_component_or_type(cls, values):
        if not values.get("component") and values.get("type"):
            values["component"] = values.get("type")
        if not values.get("type") and values.get("component"):
            values["type"] = values.get("component")
        return values


class RequirementsSchema(BaseModel):
    prerequisites: Optional[List[str]] = None
    corequisites: Optional[List[str]] = None
    antirequisites: Optional[List[str]] = None


class CourseSchema(BaseModel):
    id: str
    course_id: int
    name: NameSchema
    class_number: Optional[int] = None
    year: str
    term: str
    campus: str
    units: int
    university_wide_elective: bool
    requirements: RequirementsSchema
    class_list: List[ClassTypeSchema]
