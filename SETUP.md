# Setup and Installation Guide

## Prerequisites

- Python 3.11 or newer
- Git
- pip (Python package manager)

## Local Development Setup

### 1. Clone the Repository

```bash
git clone https://github.com/Ganesh96BCA/CloudVideoFingerprint.git
cd CloudVideoFingerprint
```

### 2. Create Virtual Environment

**On Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**On macOS/Linux:**
```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Copy the example environment file:
```bash
cp .env.example .env
```

Edit `.env` with your configuration (Supabase credentials if using cloud):
```
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_KEY=your_service_role_key
```

### 5. Run the Application

```bash
uvicorn app.main:app --reload
```

The application will start at `http://127.0.0.1:8000`

## Using the Application

### Customer Dashboard
1. Navigate to `http://127.0.0.1:8000`
2. Select the **Customer dashboard** tab
3. Enter a customer ID
4. Click **Enable camera** and allow camera permission
5. Click **Start live recording** to begin recording
6. Click **Stop** to finish and upload

### Insurance Dashboard
1. Navigate to `http://127.0.0.1:8000`
2. Select the **Insurance dashboard** tab
3. Enter a customer ID to search for evidence
4. Click on videos to verify integrity
5. Review the verification report

## Cloud Setup with Supabase

### 1. Create Supabase Project

1. Go to [supabase.com](https://supabase.com)
2. Create a new project
3. Note your project URL and service role key

### 2. Create Database Schema

1. In Supabase dashboard, open the SQL editor
2. Run the contents of `supabase_schema.sql`

### 3. Configure Environment

Update your `.env` file:
```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key
```

### 4. Restart Application

```bash
uvicorn app.main:app --reload
```

## Running Tests

```bash
python -m pytest -q
```

For detailed test output:
```bash
python -m pytest -v
```

## Video Integrity Evaluation

### Generate Test Dataset

```bash
python make_test_dataset.py
```

Or with your own video:
```bash
python make_test_dataset.py --original your_video.mp4 --other other_video.mp4
```

### Run Video Scenario Evaluation

```bash
python evaluate_video_scenarios.py dataset\evaluation_manifest.csv --output evaluation-results
```

### Run Network Scenario Evaluation

```bash
python evaluate_network_scenarios.py
```

See `VIDEO_INTEGRITY_EVALUATION.md` for detailed results and analysis.

## Optional: Install TLSH

For advanced fuzzy hashing (requires C++ compiler):

```bash
pip install python-tlsh
```

## Troubleshooting

### Camera Permission Issues
- Ensure you allow camera permission when prompted
- Check browser settings for site permissions

### Database Connection Issues
- Verify `.env` file has correct Supabase credentials
- Check that Supabase schema has been created
- Review application logs for error messages

### Import Errors
- Ensure virtual environment is activated
- Run `pip install -r requirements.txt` again
- Check Python version is 3.11 or newer

### Port Already in Use
If port 8000 is already in use:
```bash
uvicorn app.main:app --reload --port 8001
```

## File Structure

```
CloudVideoFingerprint/
├── app/                          # Main application
│   ├── main.py                  # FastAPI application
│   └── storage.py               # Storage interface
├── web/                          # Frontend
│   └── index.html               # Dashboard UI
├── data/                         # Data storage
│   ├── fingerprints.db          # SQLite database (local fallback)
│   └── media/                   # Uploaded media files
├── dataset/                      # Test datasets
├── evaluation-results/           # Evaluation output
├── tests/                        # Test suite
├── requirements.txt              # Python dependencies
├── supabase_schema.sql          # Database schema
└── README.md                     # Project documentation
```

## Support

For issues, questions, or contributions, please refer to `CONTRIBUTING.md`.
