from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class Subject(Base):
    __tablename__ = "subjects"
    id = Column(String, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    courses = relationship("Course", backref="subject_ref")


class Course(Base):
    __tablename__ = "courses"
    id = Column(String, primary_key=True)
    course_id = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)
    terms = Column(String, nullable=False)
    subject = Column(String, ForeignKey("subjects.name"), nullable=False)
    course_code = Column(String, nullable=False)
    title = Column(String, nullable=False)
    campus = Column(String, nullable=False)
    level_of_study = Column(String, nullable=True)
    units = Column(Integer, nullable=False)
    course_coordinator = Column(String, nullable=True)
    course_level = Column(String, nullable=False)
    course_overview = Column(String, nullable=True)
    prerequisites = Column(String, nullable=False)
    corequisites = Column(String, nullable=False)
    antirequisites = Column(String, nullable=False)
    university_wide_elective = Column(Boolean, nullable=False)
    url = Column(String, nullable=False)
    course_outline_url = Column(String, nullable=True)
    textbooks = Column(String, nullable=True)
    content_hash = Column(String, nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("year", "course_id", name="uq_courses_year_course_id"),
        Index("idx_courses_year_id", "year", "id"),
        Index("idx_courses_year_subject", "year", "subject"),
        Index("idx_courses_year_code", "year", "course_code"),
    )

    learning_outcomes = relationship(
        "LearningOutcome", backref="course", cascade="all, delete-orphan"
    )
    assessments = relationship(
        "Assessment", backref="course", cascade="all, delete-orphan"
    )
    course_classes = relationship(
        "CourseClass", backref="course", cascade="all, delete-orphan"
    )


class LearningOutcome(Base):
    __tablename__ = "learning_outcomes"
    id = Column(String, primary_key=True)
    year = Column(Integer, nullable=False, default=2026)
    course_id = Column(String, ForeignKey("courses.id"), nullable=False)
    description = Column(String, nullable=False)
    outcome_index = Column(Integer, nullable=False)

    __table_args__ = (Index("idx_learning_outcomes_year", "year"),)


class Assessment(Base):
    __tablename__ = "assessments"
    id = Column(String, primary_key=True)
    year = Column(Integer, nullable=False, default=2026)
    course_id = Column(String, ForeignKey("courses.id"), nullable=False)
    title = Column(String, nullable=False)
    weighting = Column(String, nullable=True)
    hurdle = Column(String, nullable=True)
    learning_outcomes = Column(String, nullable=True)

    __table_args__ = (Index("idx_assessments_year", "year"),)


class Meetings(Base):
    __tablename__ = "meetings"
    id = Column(String, primary_key=True)
    year = Column(Integer, nullable=False, default=2026)
    dates = Column(String, nullable=False)
    days = Column(String, nullable=False)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    campus = Column(String, nullable=False)
    location = Column(String, nullable=False)
    instructor = Column(String, nullable=True)
    course_class_id = Column(String, ForeignKey("course_classes.id"), nullable=False)

    __table_args__ = (Index("idx_meetings_year", "year"),)


class CourseClass(Base):
    __tablename__ = "course_classes"
    id = Column(String, primary_key=True)
    year = Column(Integer, nullable=False, default=2026, index=True)
    class_nbr = Column(Integer, nullable=False)
    section = Column(String, nullable=False)
    size = Column(Integer, default=0, nullable=False)
    available = Column(Integer, default=0, nullable=False)
    component = Column(String, nullable=True)
    group = Column(String, nullable=True)
    course_id = Column(
        String, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "year",
            "course_id",
            "class_nbr",
            "section",
            "group",
            name="uq_course_classes_year_course_class_section_group",
        ),
        Index("idx_course_classes_lookup", "year", "course_id", "class_nbr"),
        Index("idx_course_classes_year_course", "year", "course_id"),
    )

    meetings = relationship(
        "Meetings", backref="course_class", cascade="all, delete-orphan"
    )
