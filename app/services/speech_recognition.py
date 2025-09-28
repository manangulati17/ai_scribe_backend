import json
import logging
import os
from typing import Optional, Dict, Any
import numpy as np

logger = logging.getLogger(__name__)

class MockRecognizer:
    def __init__(self):
        self.partial_count = 0

    def AcceptWaveform(self, data:bytes) -> bool:
        self.partial_count +=1
        return self.partial_count % 10 == 0

    def Result(self) -> str:
        return json.dumps({
            "text": "Mock final transcription result",
            "confidence": 0.95,
            "words": []
        })
    
    def PartialResult(self) -> str:
        return json.dumps({
            "partial": f"Mock partial result {self.partial_count}"
        })
    
    def FinalResult(self) -> str:
        return json.dumps({
            "text": "Mock final session result",
            "confidence": 0.92,
            "words": []
        })

class SpeechRecognitionService:
    def __init__(self, model_path: Optional[str]=None, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.model = None
        self.recognizer = None
        self.use_mock = True


        self._load_model(model_path)

    def _load_model(self, model_path: Optional[str] = None)->None:
        try:
            import vosk

            if model_path is None:
                possible_paths = [
                    "models/vosk-model-en-us-0.22",
                    "models/vosk-model-small-en-us-0.15",
                    "/opt/vosk-model",
                    "vosk-model"
                ]

                for path in possible_paths:
                    if os.path.exists(path):
                        model_path = path
                        break

                if model_path is None:
                    logger.error("No valid model path found. Please provide a valid model path.")
                    raise ValueError("No valid model path found. Please provide a valid model path.")

            if not os.path.exists(model_path):
                logger.error(f"Model path {model_path} does not exist.")
                raise FileNotFoundError(f"Model path {model_path} does not exist.")

            self.model = vosk.Model(model_path)
            self.use_mock = False

        except ImportError:
            logger.error("Vosk library not found. Please install it using 'pip install vosk'.")
            self.use_mock = True

        except Exception as e:
            logger.error(f"Error loading model: {e}")
            self.use_mock = True
            self.model = None
            raise e
    
    def create_recognizer(self):
        if self.use_mock:
            return MockRecognizer()
        

        if self.model is None:
            logger.error("Model not loaded. Please load the model first.")
            return None
        
        try:
            import vosk
            recognizer =  vosk.KaldiRecognizer(self.model, self.sample_rate)

            recognizer.SetWords(True)
            return recognizer

        except Exception as e:
            logger.error(f"failed to create recognizer: {e}")
            return None

    def process_audio_chunk(self, recognizer, audio_data: bytes) -> Dict[str, Any]:
        if recognizer is None:
            return {'error': 'Recognizer not initialized'}

        try:
            if self.use_mock:
                if recognizer.AcceptWaveform(audio_data):
                    result = json.loads(recognizer.Result())
                    return {
                        "type": "final",
                        "text": result.get("text", ""),
                        "confidence": result.get("confidence", 0.0),
                        "words": result.get("words", [])
                    }
                else:
                    partial = json.loads(recognizer.PartialResult())
                    return {
                        "type": "partial", 
                        "text": partial.get("partial", "")
                    }
            else:
                audio_array = np.frombuffer(audio_data, dtype=np.int16)

                if recognizer.AcceptWaveform(audio_array.tobytes()):
                    result = json.loads(recognizer.Result())
                    return {
                        "type": "final",
                        "text": result.get("text", ""),
                        "confidence": result.get("confidence", 0.0),
                        "words": result.get("words", [])
                    }
                else:
                    partial = json.loads(recognizer.PartialResult())
                    return {
                        "type": "partial",
                        "text": partial.get("partial", "")
                    }
        except Exception as e:
            logger.error(f"Error processing audio chunk: {e}")
            return {'error': str(e)}
    
    def finalize_recognition(self, recognizer) -> Dict[str, Any]:
        """Get final recognition result"""
        if recognizer is None:
            return {"error": "No recognizer available"}
        
        try:
            final_result = json.loads(recognizer.FinalResult())
            return {
                "type": "final",
                "text": final_result.get("text", ""),
                "confidence": final_result.get("confidence", 0.0),
                "words": final_result.get("words", [])
            }
        except Exception as e:
            logger.error(f"Error getting final result: {e}")
            return {"error": str(e)}
    
    def is_available(self) -> bool:
        """Check if speech recognition is available"""
        return True