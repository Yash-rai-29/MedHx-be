# Chatbot — Flutter Quick Reference

All requests: `Authorization: Bearer <firebase_id_token>`  
Base: `https://<host>/chatbot`

---

## When to call what

```
User opens chat list      →  GET  /sessions
User taps "New Chat"      →  POST /sessions            → save session_id
User opens a session      →  GET  /sessions/{id}       → render messages[]
User sends a message      →  POST /sessions/{id}/ask/stream   (SSE preferred)
User deletes a session    →  DELETE /sessions/{id}
One-off question (no history needed)  →  POST /ask/stream
```

---

## 1. Create Session

```
POST /sessions
{ "title": null }
```

```json
// 201
{
  "id":            "sess_xyz789",
  "patient_id":    "uid_001",
  "title":         null,
  "message_count": 0,
  "created_at":    "2025-01-15T10:00:00Z",
  "updated_at":    "2025-01-15T10:00:00Z"
}
```

`title` is `null` until the first message. Auto-set by Gemini after the first exchange.

---

## 2. List Sessions

```
GET /sessions
```

```json
// 200  — newest first
[
  {
    "id":            "sess_xyz789",
    "patient_id":    "uid_001",
    "title":         "HbA1c levels and diet",
    "message_count": 4,
    "created_at":    "2025-01-15T10:00:00Z",
    "updated_at":    "2025-01-15T10:30:00Z"
  }
]
```

---

## 3. Session History

```
GET /sessions/{session_id}
```

```json
// 200
{
  "id":         "sess_xyz789",
  "patient_id": "uid_001",
  "title":      "HbA1c levels and diet",
  "created_at": "2025-01-15T10:00:00Z",
  "updated_at": "2025-01-15T10:30:00Z",
  "messages": [
    {
      "role":       "user",
      "content":    "What does my HbA1c of 6.1% mean?",
      "created_at": "2025-01-15T10:15:00Z",
      "sources":    []
    },
    {
      "role":       "model",
      "content":    "Your HbA1c of 6.1% is near the pre-diabetic threshold...",
      "created_at": "2025-01-15T10:15:01Z",
      "sources": [
        {
          "id":       "doc_abc123",
          "title":    "Blood Test Jan 2025",
          "filename": "blood_test_jan25.pdf",
          "type":     "lab_report"
        }
      ]
    }
  ]
}
```

`sources` on user turns is always `[]`. Render `content` as Markdown on model turns.

---

## 4. Send Message — SSE Stream (preferred)

```
POST /sessions/{session_id}/ask/stream
{ "prompt": "What does my HbA1c mean?" }
```

Response: `text/event-stream`. Each line: `data: <json>\n\n`

| `type`    | Payload                        | Action                              |
|-----------|--------------------------------|-------------------------------------|
| `sources` | `{ "sources": [ChatCitation] }`| Show citation chips                 |
| `chunk`   | `{ "content": "..." }`         | Append to AI bubble (streaming)     |
| `done`    | `{}`                           | Finalize bubble; turns saved to DB  |
| `error`   | `{ "message": "..." }`         | Show error state                    |

**Flutter snippet**

```dart
final req = http.Request('POST', Uri.parse('$base/sessions/$sessionId/ask/stream'));
req.headers['Authorization'] = 'Bearer $token';
req.headers['Content-Type']  = 'application/json';
req.body = jsonEncode({'prompt': prompt});

final streamed = await client.send(req);
final lines = streamed.stream
    .transform(utf8.decoder)
    .transform(const LineSplitter());

await for (final line in lines) {
  if (!line.startsWith('data: ')) continue;
  final event = jsonDecode(line.substring(6)) as Map<String, dynamic>;
  switch (event['type']) {
    case 'sources':
      setCitations((event['sources'] as List).map(ChatCitation.fromJson).toList());
    case 'chunk':
      appendToBuffer(event['content'] as String);
    case 'done':
      finalizeMessage();
    case 'error':
      showError(event['message']);
  }
}
```

---

## 5. Delete Session

```
DELETE /sessions/{session_id}
```

```json
// 200
{ "id": "sess_xyz789", "message": "Session deleted successfully." }
```

`403` — session belongs to another patient.  
`404` — session not found.

---

## 6. Voice — WebSocket

**When:** User holds the mic button for real-time voice conversation inside a session.

```
WS /sessions/{session_id}/ws/voice?token={firebase_id_token}
```

Token goes in the query string (WebSocket handshake does not support custom headers in Flutter).

### Frame types

**Client → Server**

| Frame | When | Content |
|-------|------|---------|
| Binary | User releases mic | Raw WAV bytes (16 kHz, mono) |
| Text JSON | Optional text shortcut | `{"prompt": "any text"}` |

**Server → Client**

| Frame | Content | Action |
|-------|---------|--------|
| Text JSON `event: response` | `{"event":"response","user_text":"...","ai_text":"...","sources":[...]}` | Render both bubbles |
| Text JSON `event: silence` | `{"event":"silence","message":"..."}` | Show "no speech detected" hint |
| Text JSON `event: error` | `{"event":"error","message":"..."}` | Show error snackbar |
| Binary | MP3 bytes | Play audio reply |

### Message flow

```
connect()
  │
  ├─ user holds mic button   →  startRecording()
  ├─ user releases mic       →  stopRecording() → send WAV bytes (binary frame)
  │
  │   server: STT → RAG → Gemini → TTS
  │
  ├─ receive text frame      →  render user + AI bubbles, show citations
  └─ receive binary frame    →  play MP3 audio
```

### Flutter snippet

```dart
// pubspec.yaml: web_socket_channel, record, just_audio, path_provider

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:record/record.dart';
import 'package:just_audio/just_audio.dart';
import 'package:path_provider/path_provider.dart';

class VoiceChatService {
  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  final AudioRecorder _recorder = AudioRecorder();
  final AudioPlayer   _player   = AudioPlayer();

  // Callbacks — wire to your setState / Riverpod / Bloc
  void Function(String userText, String aiText, List sources)? onResponse;
  void Function(Uint8List mp3)?                                 onAudio;
  void Function(String msg)?                                    onError;

  Future<void> connect(String sessionId, String firebaseToken) async {
    final uri = Uri.parse(
      'wss://<host>/chatbot/sessions/$sessionId/ws/voice?token=$firebaseToken',
    );
    _channel = WebSocketChannel.connect(uri);
    await _channel!.ready;

    _sub = _channel!.stream.listen(
      (msg) {
        if (msg is String) {
          final data = jsonDecode(msg) as Map<String, dynamic>;
          if (data['event'] == 'response') {
            onResponse?.call(
              data['user_text'] ?? '',
              data['ai_text']   ?? '',
              data['sources']   ?? [],
            );
          } else if (data['event'] == 'error' || data['event'] == 'silence') {
            onError?.call(data['message'] ?? '');
          }
        } else if (msg is List<int>) {
          onAudio?.call(Uint8List.fromList(msg));
        }
      },
      onError: (e) => onError?.call('WebSocket error: $e'),
      onDone:  () => onError?.call('Disconnected'),
    );
  }

  // Call on mic-button press
  Future<void> startRecording() async {
    if (!await _recorder.hasPermission()) return;
    final dir  = await getTemporaryDirectory();
    final path = '${dir.path}/voice_query.wav';
    await _recorder.start(
      const RecordConfig(encoder: AudioEncoder.wav, sampleRate: 16000, numChannels: 1),
      path: path,
    );
  }

  // Call on mic-button release
  Future<void> stopAndSend() async {
    final path = await _recorder.stop();
    if (path == null) return;
    final bytes = await File(path).readAsBytes();
    _channel?.sink.add(bytes);   // binary frame → server STT
  }

  // Play the MP3 bytes received from server
  Future<void> playAudio(Uint8List mp3) async {
    final dir  = await getTemporaryDirectory();
    final file = File('${dir.path}/ai_reply.mp3')..writeAsBytesSync(mp3);
    await _player.setFilePath(file.path);
    await _player.play();
  }

  void disconnect() {
    _sub?.cancel();
    _channel?.sink.close();
    _recorder.dispose();
    _player.dispose();
  }
}
```

**Usage in a widget**

```dart
final voice = VoiceChatService();

// Wire callbacks
voice.onResponse = (userText, aiText, sources) => setState(() {
  messages.add(ChatMessage(role: 'user',  content: userText, sources: []));
  messages.add(ChatMessage(role: 'model', content: aiText,
      sources: sources.map(ChatCitation.fromJson).toList()));
});
voice.onAudio = (mp3) => voice.playAudio(mp3);
voice.onError = (msg) => showSnackBar(msg);

// Connect once when screen opens
await voice.connect(sessionId, await FirebaseAuth.instance.currentUser!.getIdToken());

// Mic button
GestureDetector(
  onLongPressStart: (_) => voice.startRecording(),
  onLongPressEnd:   (_) => voice.stopAndSend(),
  child: MicIcon(),
)

// Disconnect when screen closes
@override
void dispose() { voice.disconnect(); super.dispose(); }
```

---

## Dart Models

```dart
class ChatCitation {
  final String  id;        // use with GET /documents/{id}
  final String  title;
  final String? filename;  // original upload name, e.g. blood_test_jan25.pdf
  final String? type;      // lab_report | prescription | imaging | other
}

class ChatMessage {
  final String             role;       // "user" | "model"
  final String             content;    // Markdown (model turns)
  final DateTime           createdAt;
  final List<ChatCitation> sources;    // always [] for user turns
}

class ChatSession {
  final String    id;
  final String    patientId;
  final String?   title;          // null until first message
  final int       messageCount;
  final DateTime  createdAt;
  final DateTime  updatedAt;
}
```
