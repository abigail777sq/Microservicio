# Reports Service (Local)

This repository contains a small FastAPI service `reports_service_local.py` that generates LaTeX reports using OpenAI and compiles them to PDF via `pdflatex`.

## Requirements

- Python 3.10+ (recommended)
- A LaTeX distribution installed on Windows (MiKTeX or TeX Live) so `pdflatex` is available on PATH.
  - Option (Chocolatey): `choco install miktex` (run as admin)
  - Or download and install MiKTeX from https://miktex.org/ or TeX Live from https://tug.org/texlive/

## Python dependencies

Install in a virtual environment (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

## Environment variables

The project uses a `.env` file and `python-dotenv` to load the `OPENAI_API_KEY`. There is currently an API key in `.env` in this repository — this is a secret and should be removed from version control. Replace it with your own key or set the environment variable before running:

```powershell
$env:OPENAI_API_KEY = "sk-your-key"
```

### S3 configuration (optional)

This project can upload generated `.tex` and `.pdf` files to an S3 bucket. By default it will use the bucket `ocr-files-db` and the prefix `pdf/` and will place files under `pdf/{tenant_id}/{report_id}.pdf`.

Set these environment variables if you want to customize or to ensure credentials are available:

```powershell
$env:S3_BUCKET = "ocr-files-db"
$env:S3_PREFIX = "pdf/"
# Standard AWS creds (or configure via AWS CLI / shared config)
$env:AWS_ACCESS_KEY_ID = "your_aws_key"
$env:AWS_SECRET_ACCESS_KEY = "your_aws_secret"
$env:AWS_DEFAULT_REGION = "us-east-1"
```

If no AWS credentials are set, boto3 will fall back to the default credential provider chain (profile, ECS/EC2 roles, etc.).

## Using a Postgres RDS database

The service supports using a Postgres database (for example AWS RDS). It prefers a full `DATABASE_URL` environment variable, but you can also set these individual env vars:

```powershell
$env:DB_HOST = "aicfo-pg.cyb8ec4ca9b9.us-east-1.rds.amazonaws.com"
$env:DB_PORT = "5432"
$env:DB_NAME = "aicfo"
$env:DB_USER = "app_admin"
$env:DB_PASSWORD = "<your_password>"
# or set a single DATABASE_URL:
$env:DATABASE_URL = "postgresql://app_admin:<your_password>@aicfo-pg.cyb8ec4ca9b9.us-east-1.rds.amazonaws.com:5432/aicfo"
```

Notes:
- The code will raise an error at startup if `DB_HOST` is set but `DB_NAME`, `DB_USER`, or `DB_PASSWORD` are missing (prevents partial misconfiguration).
- Install the Postgres driver before running if you use Postgres:

```powershell
pip install psycopg2-binary
```

- Do not commit credentials to version control. If you accidentally committed secrets, rotate them immediately.

## Run the service

Start the FastAPI app using Uvicorn:

```powershell
uvicorn reports_service_local:app --reload --host 0.0.0.0 --port 8000
```

The service exposes these endpoints:
- `POST /reports/generate` - generate a report (JSON body, see example below)
- `GET /reports/{report_id}` - get report metadata
- `GET /reports/download/{report_id}` - download compiled PDF
- `GET /healthz` - health and output directory

### Example request (PowerShell)

```powershell
$body = @{
  tenant_id = "11111111-1111-1111-1111-111111111111"
  type = "financiero"
  period = "2025-Q3"
  params = @{ ventas = 100000; costos = 60000; utilidad = 40000; comentario = "Buen trimestre" }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Uri http://127.0.0.1:8000/reports/generate -Method Post -Body $body -ContentType "application/json"
```

## Notes & Troubleshooting

- If `pdflatex` is not found, install a LaTeX distribution and ensure `pdflatex` is on your PATH.
- The script uses OpenAI's Chat Completions API; ensure your key has the correct access and quota.
- If you modify database models, the SQLite file is `reports_local.db` in the repo root.

If you want, I can now: create and activate a virtual environment, install the requirements, and attempt to run the app and report back with any errors. Let me know if you want me to proceed with those steps.