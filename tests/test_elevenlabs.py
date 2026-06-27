import pytest
import httpx
from unittest.mock import patch, MagicMock, AsyncMock
from common_code.config import settings
from common_code.gcp_clients import synthesize_speech, transcribe_audio

@pytest.mark.anyio
async def test_elevenlabs_tts_and_stt():
    # Configure ElevenLabs mock key
    settings.ELEVENLABS_API_KEY = "test-elevenlabs-key"
    
    # 1. Mock TTS HTTP POST call (synchronous post)
    mock_response_tts = MagicMock()
    mock_response_tts.status_code = 200
    mock_response_tts.content = b"ELEVENLABS_AUDIO_BYTES"
    
    with patch("httpx.post", return_value=mock_response_tts) as mock_post:
        audio_content = synthesize_speech("Test speech synthesis")
        assert audio_content == b"ELEVENLABS_AUDIO_BYTES"
        mock_post.assert_called_once()
        
    # 2. Mock STT HTTP POST call (asynchronous post)
    mock_response_stt = MagicMock()
    mock_response_stt.status_code = 200
    mock_response_stt.json.return_value = {"text": "Hello this is a transcription"}
    
    # Mock AsyncClient context manager and request methods
    mock_async_client = AsyncMock()
    mock_async_client.post.return_value = mock_response_stt
    mock_async_client.__aenter__.return_value = mock_async_client
    mock_async_client.__aexit__.return_value = False
    
    # Mock GCP storage client download
    mock_storage = MagicMock()
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.return_value = b"MOCK_MP3_AUDIO_DATA"
    
    mock_storage.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob
    
    with patch("common_code.gcp_clients._get_storage", return_value=mock_storage), \
         patch("httpx.AsyncClient", return_value=mock_async_client):
        transcription_resp = await transcribe_audio("gs://test-bucket/reports/audio_file.mp3")
        
    assert transcription_resp["full_text"] == "Hello this is a transcription"
    assert transcription_resp["segments"][0]["text"] == "Hello this is a transcription"
    
    # Reset API key configuration
    settings.ELEVENLABS_API_KEY = None
