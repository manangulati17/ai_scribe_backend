import os
import wave
import numpy as np
from datetime import datetime
from typing import BinaryIO, Optional
import logging

logger = logging.getLogger(__name__)

class AudioProcessor:
    def __init__(self, sample_rate: int=16000, channels: int=1, sample_width: int=2):
        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width = sample_width

    def create_audio_file(self, session_id: str) -> tuple[str, str]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{session_id}_{timestamp}.wav"

        audio_dir = "static/audio"
        os.makedirs(audio_dir, exist_ok = True)

        filepath = os.path.join(audio_dir, filename)
        file_url = f"/static/audio/{filename}"

        return filepath, file_url

    def initialize_wav_file(self, filepath: str) -> BinaryIO:
        try:
            wavFile = wave.open(filepath, "wb")
            wavFile.setnchannels(self.channels)
            wavFile.setsampwidth(self.sample_width)
            wavFile.setframerate(self.sample_rate)
            return wavFile
        except Exception as e:
            logger.error(f"Error initializing WAV file: {e}")
            raise e 

    def append_pcm_data(self, wavFile: BinaryIO, pcm_data: bytes)-> None:
        try:
            wavFile.writeframes(pcm_data)
        except Exception as e:
            logger.error(f"Error appending PCM data: {e}")
            raise e
            
    def validate_pcm_data(self, pcm_data: bytes)-> bool:
        if len(pcm_data) == 0:
            logger.warning("Empty PCM data received.")
            return False
        if len(pcm_data)%2 != 0:
            logger.warning("Invalid PCM data length. Expected even number of bytes.")
            return False
        return True

    def convert_to_numpy(self, pcm_data: bytes) -> np.ndarray:
        try:
            audio_array = np.frombuffer(pcm_data, dtype=np.int16)
            return audio_array.astype(np.float32)/32768.0
        except Exception as e:
            logger.error(f"Error converting PCM data to numpy array: {e}")
            raise e

    def get_audio_duration(self, pcm_data_length: int) -> float:
        samples = pcm_data_length // self.sample_width
        return samples / self.sample_rate

    def cleanup_file(self, filepath: str)-> bool:
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                return True
            return False
        except Exception as e:
            logger.error(f"Error cleaning up file: {e}")
            return False

