from typing import List, Literal, Optional

from pydantic import BaseModel, root_validator


class NameSchema(BaseModel):
    subject: str
    code: str
    title: str


class DateRageSchema(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None


class TimeRageSchema(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None


class MeetingSchema(BaseModel):
    day: Optional[str] = None
    location: Optional[str] = None
    instructor: Optional[str] = None
    date: Optional[DateRageSchema] = None
    time: Optional[TimeRageSchema] = None


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


class AssessmentSchema(BaseModel):
    title: str
    weighting: Optional[str] = None
    hurdle: Optional[str] = None
    learning_outcomes: Optional[str] = None


class LearningOutcomeSchema(BaseModel):
    description: str
    outcome_index: int


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
    course_coordinator: Optional[str] = None
    course_overview: str
    level_of_study: str
    course_outline_url: Optional[str] = None
    learning_outcomes: Optional[List[LearningOutcomeSchema]] = None
    textbooks: Optional[str] = None
    assessments: Optional[List[AssessmentSchema]] = None
    requirements: RequirementsSchema
    class_list: List[ClassTypeSchema]
