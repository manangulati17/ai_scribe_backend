from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), nullable=False, index=True, unique=True)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)
    age =  Column(Integer, nullable=False)
    gender = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    number = Column(String(255), nullable=False)
    patients = relationship("Patient", back_populates="user")
    sessions = relationship("Session", back_populates="user")


class Patient(Base):
    __tablename__ = "patients"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    age = Column(Integer, nullable=False)
    gender = Column(String(255), nullable=False)
    number = Column(String(255), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    user = relationship("User", back_populates="patients")
    sessions = relationship("Session", back_populates="patient")

class Session(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=True)
    date = Column(DateTime, nullable=False)
    time = Column(DateTime, nullable=False)
    duration = Column(Integer, nullable=True)
    status = Column(String(255), nullable=False, default="recording")
    audio_url = Column(String(255), nullable=True)
    audio_transcript = Column(Text, nullable=True)
    patient_id = Column(String, ForeignKey("patients.id"), nullable=True)
    patient = relationship("Patient", back_populates="sessions")
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    user = relationship("User", back_populates="sessions")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


    
    