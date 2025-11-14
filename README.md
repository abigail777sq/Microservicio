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
-
```markdown
# Reports Service (Local) — Flow of use

This microservice generates LaTeX reports from JSON data using OpenAI, compiles them to PDF (if a TeX engine is available), and stores artifacts directly in an S3 bucket under the `pdf/` prefix. This README explains the end-to-end flow and how to use the service.

## High-level flow

1. Start the service (FastAPI via Uvicorn).
2. POST a report request to `/reports/generate` with tenant_id, type, period and params.
3. The service:
   - Stores a Report row in the database with status="processing".
   - Calls OpenAI to produce a LaTeX document from the provided params and prompt.
   - Attempts to compile the LaTeX to PDF using `pdflatex`:
     - If `pdflatex` is available, it produces a PDF and uploads both `.tex` and `.pdf` directly to S3 at `s3://<bucket>/pdf/{tenant_id}/{report_id}.(tex|pdf)`.
     - If `pdflatex` is not available, it uploads only the `.tex` file to S3 at `s3://<bucket>/pdf/{tenant_id}/{report_id}.tex` and `storage_key_pdf` will be null.
   - Updates the Report row to `ready` (or `error` on failure) and stores the S3 paths in `storage_key_tex` and `storage_key_pdf`.
4. Clients can GET `/reports/{report_id}` to read metadata, or `/reports/download/{report_id}` to download the PDF (the endpoint returns the file when `storage_key_pdf` exists).

## Endpoints (quick)
- POST /reports/generate
  - Body: { tenant_id: UUID, type: str, period: str, params: dict, ai_prompt?: str }
  - Returns: { id, status, pdf_path } — pdf_path is the S3 path or null.
- GET /reports/{report_id} — returns the DB row
- GET /reports/download/{report_id} — returns the PDF file when available
- GET /healthz — returns status and the configured output path (if any)

## S3 layout and naming
- Bucket (default): `ocr-files-db` (configurable with `S3_BUCKET`).
- Prefix (default): `pdf/` (configurable with `S3_PREFIX`).
- Objects are uploaded under: `pdf/{tenant_id}/{report_id}.tex` and `pdf/{tenant_id}/{report_id}.pdf`.

Note: by default the service uploads directly to S3 and does not keep files on the host. If you want local copies, set `KEEP_LOCAL=true` (see the configuration section below).

## Configuration and environment variables
- `OPENAI_API_KEY` — OpenAI key used to generate LaTeX.
- `S3_BUCKET`, `S3_PREFIX` — target S3 location (defaults: `ocr-files-db`, `pdf/`).
- `DATABASE_URL` or `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` — database configuration (Postgres is supported; otherwise local SQLite is used).
- `KEEP_LOCAL` — when true, the service will also keep copies under `output_reports/` after uploading to S3. Default: false.

Security note: do NOT commit secrets to the repository. Use environment variables, secret managers, or IAM roles. If any secret was committed previously, rotate it immediately.

## Running the service (short)
1. Create a Python virtualenv and install dependencies:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```
2. Set required environment variables (example):
```powershell
# $env:OPENAI_API_KEY = 'sk-...'
# $env:S3_BUCKET = 'ocr-files-db'
# $env:S3_PREFIX = 'pdf/'
# $env:DATABASE_URL = 'postgresql://user:pass@host:5432/db'  # optional
```
3. Start the app:
```powershell
uvicorn reports_service_local:app --reload --host 0.0.0.0 --port 8000
```

## Example usage
1. POST a generate request to `/reports/generate` (see the example in the repository). The response will contain the report ID and the S3 PDF path (or null if no PDF was produced).
2. Verify objects in S3:
```powershell
aws s3 ls s3://ocr-files-db/pdf/<tenant_id>/ --recursive
```
3. Download the PDF via the `/reports/download/{report_id}` endpoint once `storage_key_pdf` is present.

## Behavior when `pdflatex` is not available
- The service will still upload the `.tex` file to S3 at `pdf/{tenant_id}/{report_id}.tex`.
- `storage_key_pdf` will be null and `/reports/download/{report_id}` will return 404 until a PDF exists.

If you need help putting a compilation worker in place (for example a small pod/container that pulls `.tex` files from S3, runs `pdflatex` and writes back the PDF), I can help design or implement that.

## Troubleshooting
- If `pdflatex` is missing, install MiKTeX or TeX Live on Windows and verify `pdflatex --version` works.
- Check logs from the FastAPI process for OpenAI errors or S3 permission problems.
- Confirm AWS permissions: the service needs PutObject on the target bucket/prefix.

---

If you'd like, I can also add a short diagram or a small sample script that watches S3 and compiles `.tex` files in a separate worker. Let me know which addition you'd prefer.
```

- If `pdflatex` is not found, install a LaTeX distribution and ensure `pdflatex` is on your PATH.
 - If `pdflatex` is not found, install a LaTeX distribution and ensure `pdflatex` is on your PATH. On Windows you can:
  - Install MiKTeX (recommended GUI installer) from https://miktex.org/
  - Or install TeX Live: https://tug.org/texlive/
  - If you use Chocolatey (admin):

```powershell
choco install miktex
```

After installation, restart your terminal and verify:

```powershell
pdflatex --version
```

If you cannot install a LaTeX distribution on the host, the service will still upload the generated `.tex` file to S3 (under `pdf/{tenant_id}/{report_id}.tex`) but no PDF will be produced. Use a machine/container with LaTeX to generate PDFs later, or run the service in an environment with TeX installed.

Config option: KEEP_LOCAL
-------------------------
By default the service uploads generated files directly to S3 under the `pdf/` prefix and does not retain them on the host. If you'd like the service to keep local copies under `output_reports/` in addition to uploading to S3, set this environment variable before starting the app:

```powershell
$env:KEEP_LOCAL = "true"
```

When `KEEP_LOCAL` is not set (default), the temporary files used to generate the PDF are removed after upload and only the S3 objects remain.
- The script uses OpenAI's Chat Completions API; ensure your key has the correct access and quota.
- If you modify database models, the SQLite file is `reports_local.db` in the repo root.

If you want, I can now: create and activate a virtual environment, install the requirements, and attempt to run the app and report back with any errors. Let me know if you want me to proceed with those steps.