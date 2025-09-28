from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session as DBSession
from typing import List

from app.core.database import get_db
from app.models import Patient, User
from app.schemas import PatientCreate, Patient as PatientSchema
from app.api.login import get_current_user

router = APIRouter()

@router.get('/patients', response_model=List[PatientSchema])
async def get_patients(
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    """Get all patients for the current user"""
    patients = db.query(Patient).filter(Patient.user_id == current_user.id).all()
    return patients

@router.post('/patients', response_model=PatientSchema)
async def create_patient(
    patient_data: PatientCreate,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    """Create a new patient"""
    patient = Patient(
        **patient_data.dict(),
        user_id=current_user.id
    )
    
    db.add(patient)
    db.commit()
    db.refresh(patient)
    
    return patient

@router.get('/patients/{patient_id}', response_model=PatientSchema)
async def get_patient(
    patient_id: str,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    """Get a specific patient"""
    patient = db.query(Patient).filter(
        Patient.id == patient_id,
        Patient.user_id == current_user.id
    ).first()
    
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    return patient