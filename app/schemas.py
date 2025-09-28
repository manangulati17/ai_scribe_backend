from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

# User schemas
class UserBase(BaseModel):
    email: EmailStr
    name: str
    age: int
    gender: str
    number: str

class UserCreate(UserBase):
    password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class User(UserBase):
    id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

# Patient schemas
class PatientBase(BaseModel):
    name: str
    age: int
    gender: str
    number: str

class PatientCreate(PatientBase):
    pass

class Patient(PatientBase):
    id: str
    user_id: str

    class Config:
        from_attributes = True

# Session schemas
class SessionBase(BaseModel):
    title: str
    summary: Optional[str] = None
    patient_id: str  

class SessionCreate(SessionBase):
    pass

class SessionUpdate(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    duration: Optional[int] = None
    status: Optional[str] = None
    audio_url: Optional[str] = None
    audio_transcript: Optional[str] = None

class Session(SessionBase):
    id: str
    date: datetime
    time: datetime
    duration: Optional[int] = None
    status: str
    audio_url: Optional[str] = None
    audio_transcript: Optional[str] = None
    user_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class SessionResponse(BaseModel):
    id: str
    title: str
    date: datetime
    duration: Optional[int] = None
    status: str
    audio_url: Optional[str] = None
    transcript_preview: Optional[str] = None
    patient_name: Optional[str] = None  

    class Config:
        from_attributes = True
        
        
class AudioStreamStart(BaseModel):
    patient_id: str
    title: Optional[str] = "Audio Recording Session"