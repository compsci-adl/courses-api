from sqlalchemy import (
    Column,
    Integer,
    String,
    ForeignKey,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class Subject(Base):
    __tablename__ = "subjects"
    id = Column(String, primary_key=True)
    subject_code = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=False)
    courses = relationship("Course", backref="subject_ref")


class Course(Base):
    __tablename__ = "courses"
    id = Column(String, primary_key=True)
    course_id = Column(String, nullable=False)
    course_offer_nbr = Column(Integer, nullable=False)
    year = Column(String, nullable=False)
    term = Column(String, nullable=False)
    term_descr = Column(String, nullable=False)
    subject = Column(String, ForeignKey("subjects.subject_code"), nullable=False)
    catalog_nbr = Column(String, nullable=False)
    course_title = Column(String, nullable=False)
    campus = Column(String, nullable=False)
    units = Column(Integer, nullable=False)
    class_nbr = Column(Integer, nullable=False)
    course_details = relationship("CourseDetail", backref="course")
    course_classes = relationship("CourseClass", backref="course")


class CourseDetail(Base):
    __tablename__ = "course_details"
    id = Column(String, primary_key=True)
    year = Column(String, nullable=False)
    course_id = Column(String, ForeignKey("courses.course_id"), nullable=False)
    course_offer_nbr = Column(Integer, nullable=False)
    term = Column(String, nullable=False)
    term_descr = Column(String, nullable=False)
    course_title = Column(String, nullable=False)
    campus = Column(String, nullable=False)
    campus_cd = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    catalog_nbr = Column(String, nullable=False)
    restriction = Column(String, nullable=False)
    restriction_txt = Column(String, nullable=True)
    pre_requisite = Column(String, nullable=True)
    co_requisite = Column(String, nullable=True)
    assumed_knowledge = Column(String, nullable=True)
    incompatible = Column(String, nullable=True)
    syllabus = Column(String, nullable=False)
    url = Column(String, nullable=False)


class Meetings(Base):
    __tablename__ = "meetings"
    id = Column(String, primary_key=True)
    dates = Column(String, nullable=False)
    days = Column(String, nullable=False)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    location = Column(String, nullable=False)
    course_class_id = Column(String, ForeignKey("course_classes.id"), nullable=False)


class CourseClass(Base):
    __tablename__ = "course_classes"
    id = Column(String, primary_key=True)
    class_nbr = Column(Integer, nullable=False)
    section = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    enrolled = Column(Integer, nullable=False)
    available = Column(Integer, nullable=False)
    component = Column(String, nullable=False)
    meetings = relationship("Meetings", backref="course_class")
    course_id = Column(String, ForeignKey("courses.id"), nullable=False)
