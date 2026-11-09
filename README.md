# Cloud Video Fingerprint Collector

[![Tests](https://github.com/Ganesh96BCA/CloudVideoFingerprint/workflows/Tests/badge.svg)](https://github.com/Ganesh96BCA/CloudVideoFingerprint/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A proof-of-concept cloud system for an insurance company. A browser camera acts as a vehicle dashcam and records locally only while active. When the customer stops recording, the finished video bytes, customer ID, complete-file SHA-256, and frame fingerprints are stored in the database and appear in the insurance list. One application contains both dashboards.

**Key Features:**
- 🎥 Real-time camera recording and frame fingerprinting
- 🔐 Integrity verification and tamper detection
- ☁️ Cloud storage with Supabase or local SQLite fallback
- 🚗 Insurance dashboard for evidence review
- 📊 Comprehensive integrity evaluation framework

## Architecture

```text
browser camera -> local recording + real-time frame hash -> FastAPI -> database stream
                            \-> saved video + fingerprints
                          Supabase/PostgreSQL or SQLite fallback
                      insurer clicks saved video -> integrity check
```

- `app/main.py` exposes the upload, integrity validation, streaming, and ledger APIs.
- `app/storage.py` provides one storage interface with Supabase REST as the cloud backend and SQLite as a local fallback.
- `web/index.html` contains both role views: the customer dashboard controls the live dashcam, while the insurance dashboard monitors the evidence ledger.
- `WhatsApp Image 2026-09-29 at 9.23.29 AM.jpeg` is retained as the supplied visual reference for the dashboard layout.
- `supabase_schema.sql` creates the cloud table and a service-role-only row-level security policy.

## Quick Start

The project requires Python 3.11 or newer. For detailed setup instructions, see [SETUP.md](SETUP.md).

**On Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**On macOS/Linux:**
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Usage

Open `http://127.0.0.1:8000` after starting the server.

### Customer Dashboard
1. Allow camera permission when prompted
2. Enter a customer ID
3. Click **Enable camera**, then **Start live recording**
4. During recording, only frame fingerprints are transmitted in real time; the raw video remains local
5. Click **Stop** to create a local `.webm` file and automatically upload to the insurance list
6. Optionally download a copy or upload a prerecorded video

### Insurance Dashboard
1. Enter a customer ID to search for evidence
2. Uploaded customer media appears in the media list with metadata
3. Click a saved video to verify its integrity
4. Review the verification report showing INTEGRITY VERIFIED or REVIEW REQUIRED status
5. Optionally verify selected media against stored evidence

## Getting Started

### Local Setup
See [SETUP.md](SETUP.md) for comprehensive setup and installation instructions.

### Cloud Integration
To connect with Supabase:
1. Create a Supabase project at [supabase.com](https://supabase.com)
2. Run `supabase_schema.sql` in the SQL editor
3. Copy `.env.example` to `.env` and set your credentials
4. Restart the server

Without Supabase credentials, records are persisted in `data/fingerprints.db` (local fallback).

⚠️ **Important:** The service-role key must stay on the server and must never be exposed in `web/index.html` or committed to source control.

## API overview

- `POST /api/fingerprints`: validates and stores one live camera fingerprint with customer ID and UTC timestamp.
- `POST /api/upload`: optional offline test route for a UTF-8 `.txt` fingerprint file.
- `GET /api/fingerprints?customer_id=CUS-001`: lists evidence for one customer.
- `POST /api/media`: stores an explicitly uploaded photo/video and its customer-linked fingerprints.
- `POST /api/verify`: compares manually selected incident-media fingerprints with one customer's stored ledger.
- `POST /api/validate`: reports valid and invalid SHA-256/SHA-512 lines for insurer-side validation.
- `POST /api/stream`: sends records sequentially, with a configurable delay between frames.
- `GET /api/fingerprints`: returns the stored ledger.
- `GET /api/health`: reports the active storage backend.

## Testing

```powershell
python -m pytest -q
```

For detailed output:
```powershell
python -m pytest -v
```

See [SETUP.md](SETUP.md) for more testing options.

## License

This project is licensed under the MIT License - see [LICENSE](LICENSE) file for details.

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to contribute to this project.

## Documentation

- [SETUP.md](SETUP.md) - Complete setup and installation guide
- [VIDEO_INTEGRITY_EVALUATION.md](VIDEO_INTEGRITY_EVALUATION.md) - Integrity evaluation protocols and results
- [CONTRIBUTING.md](CONTRIBUTING.md) - Contribution guidelines
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) - Community guidelines

### Evaluation

For comprehensive integrity scenario evaluation, see [VIDEO_INTEGRITY_EVALUATION.md](VIDEO_INTEGRITY_EVALUATION.md).

```powershell
python make_test_dataset.py                       # synthetic dataset (or --original my.mp4 --other other.mp4)
python evaluate_video_scenarios.py dataset\evaluation_manifest.csv --output evaluation-results
python evaluate_network_scenarios.py              # network/server failure simulation
```

The evaluation framework compares multiple hash algorithms (aHash, dHash, pHash, wHash, ssdeep, optional TLSH), vector similarity metrics (L2/L1/cosine), exact SHA-256 matching, temporal offset analysis, and processes time benchmarks with threshold sweeps.

## Attribution

- FastAPI, Uvicorn, HTTPX, python-multipart, pytest (open source; official docs).
- OpenCV (opencv-python-headless), NumPy, Pillow, SciPy, PyWavelets: frame processing and numerics.
- ImageHash (J. Buchner): aHash, dHash, pHash, wHash. Hash ideas: N. Krawetz, "Looks Like It" and "Kind of Like That" blog posts.
- ppdeep (ssdeep-compatible fuzzy hashing; ssdeep by J. Kornblum, 2006); TLSH (Oliver et al., Trend Micro, 2013), optional.
- imageio-ffmpeg / FFmpeg: video generation and transcoding (H.264, H.265).
- SHA-256 from the Python standard library (`hashlib`).
- Supabase (PostgREST) as the cloud database.

Application code in this repository is original for this project.
