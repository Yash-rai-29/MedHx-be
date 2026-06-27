# AI Companion: WebSocket Voice & Chat Endpoint Specification

This document specifies the real-time bidirectional WebSocket API for chatbot chat and voice interactions.

---

## 1. Connection Endpoint

* **URL**: `ws://<HOST>:<PORT>/chatbot/sessions/{session_id}/ws/voice?token={firebase_auth_token}`
* **Protocol**: `ws` (or `wss` for secure connections)

### Path & Query Parameters:
* `session_id` *(Path)*: The UUID of the active chat session.
* `token` *(Query)*: The patient's active Firebase ID Token (JWT). If the token is invalid or missing, the server will close the connection with code `1008` (Policy Violation).

---

## 2. Client-to-Server Messages (Inputs)

The client can send two types of frames over the WebSocket connection:

### Option A: Voice Audio Bytes (Binary Frame)
Send raw audio data as a binary frame:
* **Format**: WAV (uncompressed PCM)
* **Sample Rate**: 16000 Hz (16 kHz)
* **Channels**: Mono
* **Data Type**: Raw byte array (`Uint8List` in Flutter / Dart)

### Option B: Text Chat Messages (Text Frame)
Send a string representation of JSON or plain text:
* **Format**: JSON String
```json
{
  "prompt": "Tell me about my medicines"
}
```

---

## 3. Server-to-Client Messages (Outputs)

The server responds in two parts for each request processed:

### Part 1: Metadata Response (Text Frame)
A JSON string containing the transcribed user query (for voice), the AI reply, and references from the user's records.
```json
{
  "event": "response",
  "user_text": "What is my medicine schedule?",
  "ai_text": "Based on your records, you need to take Aspirin (1 pill) after food.",
  "sources": [
    {
      "id": "dummy_rem_0",
      "type": "medicine"
    }
  ]
}
```

### Part 2: ElevenLabs Voice Audio (Binary Frame)
Immediately following the JSON text frame, the server streams the synthesized speech audio as a binary frame:
* **Format**: MP3 audio bytes (synthesized using ElevenLabs)
* **Action**: Read this binary frame and feed it directly into your audio player buffer to speak the response.

---

## 4. Error & Status Events

The server may send error or status event text frames:

* **Silence Detected**:
  ```json
  {"event": "silence", "message": "No voice detected. Please speak clearly."}
  ```
* **Process Error**:
  ```json
  {"event": "error", "message": "Failed to process audio transcription"}
  ```
