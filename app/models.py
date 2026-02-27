from sqlalchemy import Column, String, Float, DateTime, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from .db import Base
from datetime import datetime
import uuid

class Student(Base):
    __tablename__ = "students"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name = Column(String, nullable=False)

class NeuroProfile(Base):
    __tablename__ = "neuro_profiles"
    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id"), primary_key=True)
    processing_speed = Column(Float)
    working_memory = Column(Float)
    sensory_sensitivity = Column(Float)
    switch_cost = Column(Float)
    stimulation_need = Column(Float)
    fatigue_rate = Column(Float)
    predictability_need = Column(Float)
    profile_source = Column(String)

class LessonBlock(Base):
    __tablename__ = "lesson_blocks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id"))
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime)

class TaskEvent(Base):
    __tablename__ = "task_events"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True))
    task_id = Column(UUID(as_uuid=True))
    lesson_block_id = Column(UUID(as_uuid=True))
    event_type = Column(String)
    is_correct = Column(Boolean, nullable=True)

class BlockIndex(Base):
    __tablename__ = "block_indices"
    lesson_block_id = Column(UUID(as_uuid=True), ForeignKey("lesson_blocks.id"), primary_key=True)
    overload_index = Column(Float)
    readiness_index = Column(Float)