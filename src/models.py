from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    String,
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
    course_id = Column(Integer, unique=True, nullable=False)
    year = Column(String, nullable=False)
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
    url = Column(String, nullable=False)
    course_classes = relationship("CourseClass", backref="course")


class Meetings(Base):
    __tablename__ = "meetings"
    id = Column(String, primary_key=True)
    dates = Column(String, nullable=False)
    days = Column(String, nullable=False)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    campus = Column(String, nullable=False)
    location = Column(String, nullable=False)
    course_class_id = Column(String, ForeignKey("course_classes.id"), nullable=False)


class CourseClass(Base):
    __tablename__ = "course_classes"
    id = Column(String, primary_key=True)
    class_nbr = Column(Integer, nullable=False)
    section = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    available = Column(Integer, nullable=False)
    component = Column(String, nullable=False)
    meetings = relationship("Meetings", backref="course_class")
    course_id = Column(String, ForeignKey("courses.id"), nullable=False)
