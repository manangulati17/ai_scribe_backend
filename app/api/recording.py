from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.security import HTTPBearer
from jose import JWTError, jwt
import json
import logging
import asyncio
import wave
import os
from datetime import datetime
from typing import Optional, Dict, Any

from app.services.audio_processor import AudioProcessor
from app.services.speech_recognition import SpeechRecognitionService
from app.core.database import get_db
from app.models import User, Session
from app.crud import get_user_by_email
from app.api.login import get_current_user
from sqlalchemy.orm import Session as DBSession

logger = logging.getLogger(__name__)

router = APIRouter()

SECRET_KEY = "aw1WK39TE_gfQ8U7bTSbYRZs5HtXmyhMw5ILkU9hka0"
ALGORITHM = "HS256"

audio_processor = AudioProcessor()
speech_service = SpeechRecognitionService()

security = HTTPBearer()

async def verify_websocket_token(token: str, db: DBSession) -> Optional[User]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            return None
        
        user = get_user_by_email(db, email)
        return user
        
    except JWTError:
        return None

class AudioSessionManager:
    def __init__(self):
        self.active_sessions: Dict[str, Dict] = {}
    
    async def create_session(self, websocket: WebSocket, user: User) -> str:
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{user.id}"
        
        file_path, file_url = audio_processor.create_audio_file(session_id)
        wav_file = audio_processor.initialize_wav_file(file_path)
        recognizer = speech_service.create_recognizer()
        
        if recognizer is None:
            logger.error("Failed to create speech recognizer")
            raise HTTPException(status_code=500, detail="Speech recognition not available")
        
        session_info = {
            "session_id": session_id,
            "user": user,
            "websocket": websocket,
            "file_path": file_path,
            "file_url": file_url,
            "wav_file": wav_file,
            "recognizer": recognizer,
            "created_at": datetime.now(),
            "total_audio_length": 0,
            "partial_transcript": "",
            "final_transcript": ""
        }
        
        self.active_sessions[session_id] = session_info
        logger.info(f"Created audio session: {session_id}")
        
        return session_id
    
    async def create_session_for_existing(self, websocket: WebSocket, user: User, db_session: Session) -> str:
        session_id = f"audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{user.id}"
        
        file_path, file_url = audio_processor.create_audio_file(session_id)
        wav_file = audio_processor.initialize_wav_file(file_path)
        recognizer = speech_service.create_recognizer()
        
        if recognizer is None:
            logger.error("Failed to create speech recognizer")
            raise HTTPException(status_code=500, detail="Speech recognition not available")
        
        session_info = {
            "session_id": session_id,
            "db_session_id": db_session.id,
            "user": user,
            "websocket": websocket,
            "file_path": file_path,
            "file_url": file_url,
            "wav_file": wav_file,
            "recognizer": recognizer,
            "created_at": datetime.now(),
            "total_audio_length": 0,
            "partial_transcript": "",
            "final_transcript": ""
        }
        
        self.active_sessions[session_id] = session_info
        logger.info(f"Created audio session for existing DB session: {session_id} -> {db_session.id}")
        
        return session_id
    
    async def process_audio_chunk(self, session_id: str, audio_data: bytes) -> Dict[str, Any]:
        if session_id not in self.active_sessions:
            return {"error": "Session not found"}
        
        session = self.active_sessions[session_id]
        
        try:
            if not audio_processor.validate_pcm_data(audio_data):
                return {"error": "Invalid PCM data"}
            
            audio_processor.append_pcm_data(session["wav_file"], audio_data)
            session["total_audio_length"] += len(audio_data)
            
            result = speech_service.process_audio_chunk(session["recognizer"], audio_data)
            
            if result.get("type") == "partial":
                session["partial_transcript"] = result.get("text", "")
            elif result.get("type") == "final":
                session["final_transcript"] += " " + result.get("text", "")
                session["partial_transcript"] = ""
            
            return result
            
        except Exception as e:
            logger.error(f"Error processing audio chunk for session {session_id}: {e}")
            return {"error": str(e)}
    
    async def end_session(self, session_id: str, db: DBSession) -> Dict[str, Any]:
        if session_id not in self.active_sessions:
            return {"error": "Session not found"}
        
        session = self.active_sessions[session_id]
        
        try:
            final_result = speech_service.finalize_recognition(session["recognizer"])
            final_transcript = session["final_transcript"] + " " + final_result.get("text", "")
            
            session["wav_file"].close()
            duration = audio_processor.get_audio_duration(session["total_audio_length"])
            
            # Generate summary from transcript
            summary = self._generate_summary(final_transcript.strip())
            
            # If this is linked to an existing database session, update it
            if "db_session_id" in session:
                db_session = db.query(Session).filter(Session.id == session["db_session_id"]).first()
                if db_session:
                    db_session.audio_url = session["file_url"]
                    db_session.audio_transcript = final_transcript.strip()
                    db_session.summary = summary
                    db_session.duration = int(duration)
                    db_session.status = "completed"
                    db_session.updated_at = datetime.now()
                    
                    db.commit()
                    db.refresh(db_session)
                    
                    logger.info(f"Updated existing session: {db_session.id}")
                    
                    del self.active_sessions[session_id]
                    
                    return {
                        "session_id": db_session.id,
                        "audio_url": session["file_url"],
                        "transcript": final_transcript.strip(),
                        "summary": summary,
                        "duration": duration
                    }
            
            # Fallback: create new session (legacy behavior)
            db_session = Session(
                title=f"Audio Session {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                summary=summary,
                date=session["created_at"],
                time=session["created_at"],
                duration=int(duration),
                status="completed",
                audio_url=session["file_url"],
                audio_transcript=final_transcript.strip(),
                patient_id=None,
                user_id=session["user"].id
            )
            
            db.add(db_session)
            db.commit()
            db.refresh(db_session)
            
            del self.active_sessions[session_id]
            
            logger.info(f"Ended audio session: {session_id}")
            
            return {
                "session_id": db_session.id,
                "audio_url": session["file_url"],
                "transcript": final_transcript.strip(),
                "summary": summary,
                "duration": duration
            }
            
        except Exception as e:
            logger.error(f"Error ending session {session_id}: {e}")
            if "wav_file" in session:
                session["wav_file"].close()
            if session_id in self.active_sessions:
                del self.active_sessions[session_id]
            return {"error": str(e)}
    
    def _generate_summary(self, transcript: str) -> str:
        """Generate a short summary from the transcript"""
        if not transcript or len(transcript.strip()) < 50:
            return "Brief audio recording session"
        
        # Simple summary: first 200 characters + "..."
        words = transcript.split()
        if len(words) <= 20:
            return transcript
        else:
            return " ".join(words[:20]) + "..."

session_manager = AudioSessionManager()

@router.websocket("/ws/audio-stream")
async def audio_stream_websocket(
    websocket: WebSocket,
    token: Optional[str] = None
):
    await websocket.accept()
    
    from app.core.database import SessionLocal
    db = SessionLocal()
    
    try:
        if not token:
            await websocket.send_text(json.dumps({"error": "Authentication token required"}))
            await websocket.close(code=1008)
            return
        
        user = await verify_websocket_token(token, db)
        if not user:
            await websocket.send_text(json.dumps({"error": "Invalid authentication token"}))
            await websocket.close(code=1008)
            return
        
        # Get session_id from query parameters
        session_id = websocket.query_params.get("session_id")
        if not session_id:
            await websocket.send_text(json.dumps({"error": "Session ID required in query parameters"}))
            await websocket.close(code=1008)
            return
        
        # Verify session exists and belongs to user
        existing_session = db.query(Session).filter(
            Session.id == session_id,
            Session.user_id == user.id,
            Session.status == "active"
        ).first()
        
        if not existing_session:
            await websocket.send_text(json.dumps({"error": "Session not found or not active"}))
            await websocket.close(code=1008)
            return
        
        if not speech_service.is_available():
            await websocket.send_text(json.dumps({"error": "Speech recognition service not available"}))
            await websocket.close(code=1011)
            return
        
        # Initialize audio session with existing database session
        audio_session_id = await session_manager.create_session_for_existing(websocket, user, existing_session)
        
        await websocket.send_text(json.dumps({
            "type": "session_created",
            "session_id": audio_session_id,
            "db_session_id": session_id,
            "message": "Audio session started. Send binary audio data."
        }))
        
        while True:
            try:
                message = await websocket.receive()
                
                if "bytes" in message:
                    audio_data = message["bytes"]
                    result = await session_manager.process_audio_chunk(audio_session_id, audio_data)
                    
                    if "error" not in result:
                        await websocket.send_text(json.dumps(result))
                    else:
                        await websocket.send_text(json.dumps({"error": result["error"]}))
                
                elif "text" in message:
                    try:
                        control_msg = json.loads(message["text"])
                        
                        if control_msg.get("action") == "end_session":
                            end_result = await session_manager.end_session(audio_session_id, db)
                            await websocket.send_text(json.dumps({
                                "type": "session_ended",
                                **end_result
                            }))
                            break
                            
                    except json.JSONDecodeError:
                        await websocket.send_text(json.dumps({"error": "Invalid control message format"}))
                
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected for session: {session_id}")
                break
            except Exception as e:
                logger.error(f"Error in WebSocket loop: {e}")
                await websocket.send_text(json.dumps({"error": str(e)}))
                break
    
    except Exception as e:
        logger.error(f"WebSocket connection error: {e}")
        try:
            await websocket.send_text(json.dumps({"error": str(e)}))
        except:
            pass
    
    finally:
        if 'audio_session_id' in locals():
            try:
                await session_manager.end_session(audio_session_id, db)
            except:
                pass
        
        db.close()
        
        try:
            await websocket.close()
        except:
            pass

@router.post("/sessions")
async def create_session(
    session_data: dict,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    try:
        # Extract session data
        title = session_data.get("title")
        patient_id = session_data.get("patient_id")

        logger.info(f"Creating session - Title: {title}, Patient ID: {patient_id}, User ID: {current_user.id}")
        logger.info(f"Session data received: {session_data}")
        
        if not title:
            raise HTTPException(status_code=400, detail="Session title is required")

        if patient_id:
            from app.models import Patient
            patient = db.query(Patient).filter(Patient.id == patient_id, Patient.user_id == current_user.id).first()
            if not patient:
                logger.warning(f"Patient {patient_id} not found for user {current_user.id}")
                raise HTTPException(status_code=400, detail="Invalid patient ID")
            logger.info(f"Validated patient: {patient.name} (ID: {patient.id})")
        else:
            logger.warning("No patient_id provided in session creation")
        
        # Create new session in database with "active" status
        new_session = Session(
            title=title,
            summary="recording in progress...",
            date=datetime.now(),
            time=datetime.now(),
            duration=0,
            status="active",
            audio_url="will be generated later",
            audio_transcript="",
            patient_id=patient_id,
            user_id=current_user.id
        )
        
        db.add(new_session)
        db.commit()
        db.refresh(new_session)
        
        logger.info(f"Created session: {new_session.id} for user: {current_user.id}")
        
        return {
            "id": new_session.id,
            "title": new_session.title,
            "status": new_session.status,
            "patient_id": new_session.patient_id,
            "created_at": new_session.created_at
        }
        
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        raise HTTPException(status_code=500, detail="Failed to create session")

@router.get("/sessions")
async def get_user_sessions(
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    try:
        sessions = db.query(Session).filter(Session.user_id == current_user.id).all()
        return {
            "sessions": [
                {
                    "id": session.id,
                    "title": session.title,
                    "date": session.date,
                    "duration": session.duration,
                    "status": session.status,
                    "audio_url": session.audio_url,
                    "transcript_preview": session.audio_transcript[:100] + "..." if len(session.audio_transcript) > 100 else session.audio_transcript
                }
                for session in sessions
            ]
        }
    except Exception as e:
        logger.error(f"Error getting user sessions: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve sessions")

@router.get("/sessions/{session_id}")
async def get_session_details(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    try:
        session = db.query(Session).filter(
            Session.id == session_id,
            Session.user_id == current_user.id
        ).first()
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return {
            "id": session.id,
            "title": session.title,
            "summary": session.summary,
            "date": session.date,
            "time": session.time,
            "duration": session.duration,
            "status": session.status,
            "audio_url": session.audio_url,
            "audio_transcript": session.audio_transcript,
            "patient_id": session.patient_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting session details: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve session details")

@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    try:
        session = db.query(Session).filter(
            Session.id == session_id,
            Session.user_id == current_user.id
        ).first()
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        audio_path = session.audio_url.replace("/static/", "static/")
        if os.path.exists(audio_path):
            audio_processor.cleanup_file(audio_path)
        
        db.delete(session)
        db.commit()
        
        return {"message": "Session deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete session")
