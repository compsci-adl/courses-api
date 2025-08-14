from typing import Any, List, Literal

from pydantic import BaseModel, Field


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
    day: Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    location: str
    date: DateRageSchema
    time: TimeRageSchema


class ClassSchema(BaseModel):
    number: str
    section: str #Return class section
    available_seats: str
    meetings: List[MeetingSchema]


class ClassTypeSchema(BaseModel):
    id: str
    category: Literal["enrolment", "related", "unknown"]
    type: str
    classes: List[ClassSchema]


class CourseSchema(BaseModel):
    id: str
    course_id: str
    name: NameSchema
    class_number: int
    year: int
    term: str
    campus: str
    units: int
    requirements: Any
    class_list: List[ClassTypeSchema]
