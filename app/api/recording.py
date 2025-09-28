from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.security import HTTPBearer
from jose import JWTError, jwt
import json
import logging
import asyncio
import wave
import os
from datetime import datetime
from typing import Optional, Dict, Any, List

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
        self.chunk_buffers: Dict[str, List[Dict]] = {}  # Buffer for out-of-order chunks
        self.chunk_sequences: Dict[str, int] = {}  # Track expected sequence numbers
        self.partial_sessions: Dict[str, Dict] = {}  # Store partial sessions for recovery
        self.network_interruptions: Dict[str, List[Dict]] = {}  # Track network interruptions
        self.chunk_metadata: Dict[str, Dict] = {}  # Store chunk metadata for deduplication
        self.session_timeouts: Dict[str, datetime] = {}  # Track session timeouts
    
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
    
    async def resume_session(self, websocket: WebSocket, user: User, db_session: Session, resume_point: int = 0) -> str:
        """Resume a partially completed session from a specific point"""
        session_id = f"resume_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{user.id}"
        
        # Check if we have partial data for this session
        partial_key = f"{db_session.id}_{user.id}"
        if partial_key in self.partial_sessions:
            partial_data = self.partial_sessions[partial_key]
            logger.info(f"Resuming session with partial data from chunk {resume_point}")
        else:
            logger.info(f"Creating new session for resumption from chunk {resume_point}")
            partial_data = {
                "chunks": [],
                "total_chunks": 0,
                "last_chunk": -1,
                "partial_transcript": "",
                "final_transcript": ""
            }
        
        file_path, file_url = audio_processor.create_audio_file(session_id)
        wav_file = audio_processor.initialize_wav_file(file_path)
        recognizer = speech_service.create_recognizer()
        
        if recognizer is None:
            logger.error("Failed to create speech recognizer for resumed session")
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
            "partial_transcript": partial_data.get("partial_transcript", ""),
            "final_transcript": partial_data.get("final_transcript", ""),
            "resume_point": resume_point,
            "is_resumed": True,
            "partial_chunks": partial_data.get("chunks", [])
        }
        
        self.active_sessions[session_id] = session_info
        logger.info(f"Resumed audio session: {session_id} -> {db_session.id} from chunk {resume_point}")
        
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
    
    async def process_audio_chunk(self, session_id: str, audio_data: bytes, chunk_sequence: int = None) -> Dict[str, Any]:
        if session_id not in self.active_sessions:
            return {"error": "Session not found"}
        
        session = self.active_sessions[session_id]
        
        try:
            if not audio_processor.validate_pcm_data(audio_data):
                return {"error": "Invalid PCM data"}
            
            # Handle resumed sessions with partial data
            if session.get("is_resumed", False):
                return await self._handle_resumed_chunk(session_id, audio_data, chunk_sequence)
            
            # Handle chunk sequencing for out-of-order delivery
            if chunk_sequence is not None:
                return await self._handle_sequenced_chunk(session_id, audio_data, chunk_sequence)
            else:
                # Legacy processing for non-sequenced chunks
                return await self._process_immediate_chunk(session_id, audio_data)
            
        except Exception as e:
            logger.error(f"Error processing audio chunk for session {session_id}: {e}")
            return {"error": str(e)}
    
    async def process_sequenced_audio_chunk(self, session_id: str, chunk_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process audio chunk with sequence number for network resilience support"""
        if session_id not in self.active_sessions:
            return {"error": "Session not found"}
        
        session = self.active_sessions[session_id]
        sequence_number = chunk_data.get("sequence", 0)
        audio_data = chunk_data.get("data", b"")
        timestamp = chunk_data.get("timestamp", datetime.now().timestamp())
        network_quality = chunk_data.get("network_quality", "unknown")
        chunk_size = chunk_data.get("chunk_size", len(audio_data))
        
        try:
            if not audio_processor.validate_pcm_data(audio_data):
                return {"error": "Invalid PCM data"}
            
            logger.info(f"Processing sequenced chunk {sequence_number} for session {session_id} (network: {network_quality})")
            
            # Initialize chunk tracking for this session if not exists
            if "chunk_sequences" not in session:
                session["chunk_sequences"] = {}
                session["expected_sequence"] = 0
                session["network_interruptions"] = 0
                session["out_of_order_chunks"] = 0
                session["duplicate_chunks"] = 0
            
            # Check for duplicate chunks
            chunk_key = f"{sequence_number}_{hash(audio_data)}"
            if chunk_key in self.chunk_metadata.get(session_id, {}):
                session["duplicate_chunks"] += 1
                logger.warning(f"Duplicate chunk detected: {sequence_number} for session {session_id}")
                return {"type": "duplicate", "message": f"Chunk {sequence_number} already processed"}
            
            # Store chunk metadata for deduplication
            if session_id not in self.chunk_metadata:
                self.chunk_metadata[session_id] = {}
            self.chunk_metadata[session_id][chunk_key] = {
                "sequence": sequence_number,
                "timestamp": timestamp,
                "network_quality": network_quality
            }
            
            # Store chunk with sequence number
            session["chunk_sequences"][sequence_number] = {
                "data": audio_data,
                "timestamp": datetime.now(),
                "processed": False,
                "network_quality": network_quality,
                "chunk_size": chunk_size
            }
            
            # Check for out-of-order chunks
            if sequence_number != session["expected_sequence"]:
                session["out_of_order_chunks"] += 1
                logger.info(f"Out-of-order chunk received: {sequence_number}, expected: {session['expected_sequence']}")
            
            # Process chunks in order
            processed_chunks = []
            expected_sequence = session["expected_sequence"]
            
            while expected_sequence in session["chunk_sequences"]:
                chunk_info = session["chunk_sequences"][expected_sequence]
                
                # Process this chunk
                audio_processor.append_pcm_data(session["wav_file"], chunk_info["data"])
                session["total_audio_length"] += len(chunk_info["data"])
                
                # Process for transcription
                result = speech_service.process_audio_chunk(session["recognizer"], chunk_info["data"])
                
                if result.get("type") == "partial":
                    session["partial_transcript"] = result.get("text", "")
                elif result.get("type") == "final":
                    session["final_transcript"] += " " + result.get("text", "")
                    session["partial_transcript"] = ""
                
                chunk_info["processed"] = True
                processed_chunks.append(chunk_info)
                session["expected_sequence"] += 1
                expected_sequence += 1
            
            # Clean up processed chunks
            for seq_num in list(session["chunk_sequences"].keys()):
                if session["chunk_sequences"][seq_num]["processed"]:
                    del session["chunk_sequences"][seq_num]
            
            # Update session timeout
            self.session_timeouts[session_id] = datetime.now()
            
            # Return the latest transcription result
            if processed_chunks:
                latest_result = speech_service.process_audio_chunk(
                    session["recognizer"], 
                    processed_chunks[-1]["data"]
                )
                return latest_result
            else:
                return {"type": "buffered", "message": f"Chunk {sequence_number} buffered, waiting for sequence {session['expected_sequence']}"}
            
        except Exception as e:
            logger.error(f"Error processing sequenced chunk for session {session_id}: {e}")
            return {"error": str(e)}
    
    async def _handle_sequenced_chunk(self, session_id: str, audio_data: bytes, chunk_sequence: int) -> Dict[str, Any]:
        """Handle sequenced audio chunks with out-of-order support"""
        try:
            # Initialize chunk buffer for this session if not exists
            if session_id not in self.chunk_buffers:
                self.chunk_buffers[session_id] = []
                self.chunk_sequences[session_id] = 0
            
            # Add chunk to buffer with sequence number
            chunk_info = {
                'sequence': chunk_sequence,
                'data': audio_data,
                'timestamp': datetime.now(),
                'processed': False
            }
            
            self.chunk_buffers[session_id].append(chunk_info)
            
            # Sort buffer by sequence number
            self.chunk_buffers[session_id].sort(key=lambda x: x['sequence'])
            
            # Process chunks in order
            processed_chunks = []
            session = self.active_sessions[session_id]
            
            for chunk in self.chunk_buffers[session_id][:]:
                if chunk['sequence'] == self.chunk_sequences[session_id]:
                    # Process this chunk
                    audio_processor.append_pcm_data(session["wav_file"], chunk['data'])
                    session["total_audio_length"] += len(chunk['data'])
                    
                    # Process for transcription
                    result = speech_service.process_audio_chunk(session["recognizer"], chunk['data'])
                    
                    if result.get("type") == "partial":
                        session["partial_transcript"] = result.get("text", "")
                    elif result.get("type") == "final":
                        session["final_transcript"] += " " + result.get("text", "")
                        session["partial_transcript"] = ""
                    
                    chunk['processed'] = True
                    processed_chunks.append(chunk)
                    self.chunk_sequences[session_id] += 1
                else:
                    # Stop processing if we hit a gap in sequence
                    break
            
            # Remove processed chunks from buffer
            self.chunk_buffers[session_id] = [
                chunk for chunk in self.chunk_buffers[session_id] 
                if not chunk['processed']
            ]
            
            # Return the latest transcription result
            if processed_chunks:
                latest_result = speech_service.process_audio_chunk(
                    session["recognizer"], 
                    processed_chunks[-1]['data']
                )
                return latest_result
            else:
                return {"type": "buffered", "message": f"Chunk {chunk_sequence} buffered, waiting for sequence {self.chunk_sequences[session_id]}"}
            
        except Exception as e:
            logger.error(f"Error handling sequenced chunk for session {session_id}: {e}")
            return {"error": str(e)}
    
    async def _process_immediate_chunk(self, session_id: str, audio_data: bytes) -> Dict[str, Any]:
        """Process audio chunk immediately (legacy mode)"""
        session = self.active_sessions[session_id]
        
        audio_processor.append_pcm_data(session["wav_file"], audio_data)
        session["total_audio_length"] += len(audio_data)
        
        result = speech_service.process_audio_chunk(session["recognizer"], audio_data)
        
        if result.get("type") == "partial":
            session["partial_transcript"] = result.get("text", "")
        elif result.get("type") == "final":
            session["final_transcript"] += " " + result.get("text", "")
            session["partial_transcript"] = ""
        
        return result
    
    async def _handle_resumed_chunk(self, session_id: str, audio_data: bytes, chunk_sequence: int = None) -> Dict[str, Any]:
        """Handle audio chunks for resumed sessions"""
        try:
            session = self.active_sessions[session_id]
            resume_point = session.get("resume_point", 0)
            
            # If this is a resumed session, we need to merge with existing partial data
            if chunk_sequence is not None and chunk_sequence < resume_point:
                # This chunk was already processed, skip it
                logger.info(f"Skipping chunk {chunk_sequence} for resumed session (already processed)")
                return {"type": "skipped", "message": f"Chunk {chunk_sequence} already processed"}
            
            # Process the chunk normally
            audio_processor.append_pcm_data(session["wav_file"], audio_data)
            session["total_audio_length"] += len(audio_data)
            
            # Process for transcription
            result = speech_service.process_audio_chunk(session["recognizer"], audio_data)
            
            if result.get("type") == "partial":
                session["partial_transcript"] = result.get("text", "")
            elif result.get("type") == "final":
                session["final_transcript"] += " " + result.get("text", "")
                session["partial_transcript"] = ""
            
            # Store partial data for potential future recovery
            partial_key = f"{session['db_session_id']}_{session['user'].id}"
            if partial_key not in self.partial_sessions:
                self.partial_sessions[partial_key] = {
                    "chunks": [],
                    "total_chunks": 0,
                    "last_chunk": -1,
                    "partial_transcript": "",
                    "final_transcript": ""
                }
            
            # Update partial session data
            partial_data = self.partial_sessions[partial_key]
            partial_data["chunks"].append({
                "sequence": chunk_sequence or 0,
                "data": audio_data,
                "timestamp": datetime.now()
            })
            partial_data["total_chunks"] += 1
            partial_data["last_chunk"] = chunk_sequence or 0
            partial_data["partial_transcript"] = session["partial_transcript"]
            partial_data["final_transcript"] = session["final_transcript"]
            
            logger.info(f"Processed resumed chunk {chunk_sequence} for session {session_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error handling resumed chunk for session {session_id}: {e}")
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
    
    async def handle_network_interruption(self, session_id: str, interruption_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle network interruption for a session"""
        if session_id not in self.active_sessions:
            return {"error": "Session not found"}
        
        session = self.active_sessions[session_id]
        
        try:
            # Track network interruption
            session["network_interruptions"] = session.get("network_interruptions", 0) + 1
            
            # Store interruption data
            if session_id not in self.network_interruptions:
                self.network_interruptions[session_id] = []
            
            self.network_interruptions[session_id].append({
                "timestamp": datetime.now(),
                "reason": interruption_data.get("reason", "unknown"),
                "duration": interruption_data.get("duration", 0),
                "chunks_lost": interruption_data.get("chunks_lost", 0)
            })
            
            logger.info(f"Network interruption recorded for session {session_id}: {interruption_data}")
            
            return {"type": "interruption_recorded", "message": "Network interruption recorded"}
            
        except Exception as e:
            logger.error(f"Error handling network interruption for session {session_id}: {e}")
            return {"error": str(e)}
    
    async def recover_session_from_interruption(self, session_id: str, recovery_data: Dict[str, Any]) -> Dict[str, Any]:
        """Recover session from network interruption"""
        if session_id not in self.active_sessions:
            return {"error": "Session not found"}
        
        session = self.active_sessions[session_id]
        
        try:
            # Process any buffered chunks from the interruption
            buffered_chunks = recovery_data.get("buffered_chunks", [])
            
            for chunk_data in buffered_chunks:
                await self.process_sequenced_audio_chunk(session_id, chunk_data)
            
            logger.info(f"Session {session_id} recovered from interruption with {len(buffered_chunks)} buffered chunks")
            
            return {
                "type": "recovery_completed",
                "message": f"Session recovered with {len(buffered_chunks)} buffered chunks",
                "buffered_chunks_processed": len(buffered_chunks)
            }
            
        except Exception as e:
            logger.error(f"Error recovering session {session_id} from interruption: {e}")
            return {"error": str(e)}

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
        
        # Get session_id and resume_point from query parameters
        session_id = websocket.query_params.get("session_id")
        resume_point = int(websocket.query_params.get("resume_point", "0"))
        
        if not session_id:
            await websocket.send_text(json.dumps({"error": "Session ID required in query parameters"}))
            await websocket.close(code=1008)
            return
        
        # Verify session exists and belongs to user
        existing_session = db.query(Session).filter(
            Session.id == session_id,
            Session.user_id == user.id,
            Session.status.in_(["active", "resuming", "emergency_saved"])
        ).first()
        
        if not existing_session:
            await websocket.send_text(json.dumps({"error": "Session not found or not resumable"}))
            await websocket.close(code=1008)
            return
        
        if not speech_service.is_available():
            await websocket.send_text(json.dumps({"error": "Speech recognition service not available"}))
            await websocket.close(code=1011)
            return
        
        # Initialize audio session with existing database session
        if existing_session.status == "resuming" and resume_point > 0:
            # Resume existing session
            audio_session_id = await session_manager.resume_session(websocket, user, existing_session, resume_point)
            await websocket.send_text(json.dumps({
                "type": "session_resumed",
                "session_id": audio_session_id,
                "db_session_id": session_id,
                "resume_point": resume_point,
                "message": f"Audio session resumed from chunk {resume_point}. Send binary audio data."
            }))
        else:
            # Create new session
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
                    # Extract chunk sequence if provided in message metadata
                    chunk_sequence = None
                    if hasattr(message, 'get') and message.get('chunk_sequence'):
                        chunk_sequence = message['chunk_sequence']
                    
                    result = await session_manager.process_audio_chunk(
                        audio_session_id, 
                        audio_data, 
                        chunk_sequence
                    )
                    
                    if "error" not in result:
                        await websocket.send_text(json.dumps(result))
                    else:
                        await websocket.send_text(json.dumps({"error": result["error"]}))
                
                elif "text" in message:
                    try:
                        # Check if this is a JSON message with chunk data
                        try:
                            chunk_data = json.loads(message["text"])
                            if chunk_data.get("type") == "audio_chunk":
                                # Handle sequenced audio chunk
                                result = await session_manager.process_sequenced_audio_chunk(
                                    audio_session_id, 
                                    chunk_data
                                )
                                
                                if "error" not in result:
                                    await websocket.send_text(json.dumps(result))
                                else:
                                    await websocket.send_text(json.dumps({"error": result["error"]}))
                                continue
                        except json.JSONDecodeError:
                            pass
                        
                        # Handle control messages
                        control_msg = json.loads(message["text"])
                        
                        if control_msg.get("action") == "end_session":
                            end_result = await session_manager.end_session(audio_session_id, db)
                            await websocket.send_text(json.dumps({
                                "type": "session_ended",
                                **end_result
                            }))
                            break
                        
                        elif control_msg.get("action") == "network_interruption":
                            # Handle network interruption
                            interruption_result = await session_manager.handle_network_interruption(
                                audio_session_id, 
                                control_msg.get("data", {})
                            )
                            await websocket.send_text(json.dumps(interruption_result))
                        
                        elif control_msg.get("action") == "recover_from_interruption":
                            # Handle session recovery from network interruption
                            recovery_result = await session_manager.recover_session_from_interruption(
                                audio_session_id, 
                                control_msg.get("data", {})
                            )
                            await websocket.send_text(json.dumps(recovery_result))
                            
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

@router.post("/sessions/resume")
async def resume_session(
    session_data: dict,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    try:
        # Extract session data
        session_id = session_data.get("session_id")
        resume_point = session_data.get("resume_point", 0)
        
        logger.info(f"Resuming session - Session ID: {session_id}, Resume Point: {resume_point}, User ID: {current_user.id}")
        
        if not session_id:
            raise HTTPException(status_code=400, detail="Session ID is required")
        
        # Verify session exists and belongs to user
        existing_session = db.query(Session).filter(
            Session.id == session_id,
            Session.user_id == current_user.id,
            Session.status.in_(["active", "emergency_saved"])
        ).first()
        
        if not existing_session:
            logger.warning(f"Session {session_id} not found for user {current_user.id}")
            raise HTTPException(status_code=404, detail="Session not found or not resumable")
        
        # Update session status to indicate resumption
        existing_session.status = "resuming"
        existing_session.updated_at = datetime.now()
        db.commit()
        db.refresh(existing_session)
        
        logger.info(f"Session {session_id} marked for resumption from chunk {resume_point}")
        
        return {
            "id": existing_session.id,
            "title": existing_session.title,
            "status": existing_session.status,
            "resume_point": resume_point,
            "message": "Session ready for resumption"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resuming session: {e}")
        raise HTTPException(status_code=500, detail="Failed to resume session")

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
        
        # logger.info(f"Creating session - Title: {title}, Patient ID: {patient_id}, User ID: {current_user.id}")
        # logger.info(f"Session data received: {session_data}")
        print("🎤 Backend.create_session - Creating session:")
        print(f"   Title: {title}")
        print(f"   Patient ID: {patient_id}")
        print(f"   User ID: {current_user.id}")
        print(f"   Full session data received: {session_data}")
        
        logger.info(f"🎤 Backend.create_session - Creating session:")
        logger.info(f"   Title: {title}")
        logger.info(f"   Patient ID: {patient_id}")
        logger.info(f"   User ID: {current_user.id}")
        logger.info(f"   Full session data received: {session_data}")
        
        if not title:
            raise HTTPException(status_code=400, detail="Session title is required")
        
        # Validate patient_id if provided
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
            summary="Recording in progress...",  # Provide default summary to avoid NOT NULL constraint
            date=datetime.now(),
            time=datetime.now(),
            duration=0,
            status="active",
            audio_url="",
            audio_transcript="",
            patient_id=patient_id,
            user_id=current_user.id
        )
        
        db.add(new_session)
        db.commit()
        db.refresh(new_session)
        print("✅ Backend.create_session - Session created successfully:")
        print(f"   Session ID: {new_session.id}")
        print(f"   Title: {new_session.title}")
        print(f"   Patient ID: {new_session.patient_id}")
        print(f"   Status: {new_session.status}")
        print(f"   User ID: {new_session.user_id}")
        
        logger.info(f"✅ Backend.create_session - Session created successfully:")
        logger.info(f"   Session ID: {new_session.id}")
        logger.info(f"   Title: {new_session.title}")
        logger.info(f"   Patient ID: {new_session.patient_id}")
        logger.info(f"   Status: {new_session.status}")
        logger.info(f"   User ID: {new_session.user_id}")
        
        # logger.info(f"Created session: {new_session.id} for user: {current_user.id}")
        # logger.info(f"Session details - Title: {new_session.title}, Patient ID: {new_session.patient_id}, Status: {new_session.status}")
        
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
