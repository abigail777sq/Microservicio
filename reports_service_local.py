import os
import uuid
import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import Column
from sqlalchemy.dialects.sqlite import JSON
from dotenv import load_dotenv
import boto3
from sqlmodel import SQLModel, Field, create_engine, Session, select
import openai, re

load_dotenv()

# ==================== CONFIG ====================
# Database configuration: prefer a full DATABASE_URL, then DB_* parts, otherwise fallback to local sqlite.
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    DB_URL = DATABASE_URL
else:
    DB_HOST = os.getenv("DB_HOST")
    if DB_HOST:
        DB_PORT = os.getenv("DB_PORT", "5432")
        DB_NAME = os.getenv("DB_NAME", "")
        DB_USER = os.getenv("DB_USER", "")
        DB_PASSWORD = os.getenv("DB_PASSWORD", "")
        # require the minimal set if a host is provided
        if not all([DB_NAME, DB_USER, DB_PASSWORD]):
            raise RuntimeError(
                "DB_HOST is set but DB_NAME, DB_USER and DB_PASSWORD must also be provided."
            )
        # Build a SQLAlchemy-compatible Postgres URL (psycopg2)
        DB_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    else:
        DB_URL = os.getenv("DB_URL", "sqlite:///./reports_local.db")

# Output directory (safe default)
OUTPUT_DIR = Path(os.getenv("REPORTS_OUTPUT_DIR", "./output_reports"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# OpenAI key (warn at startup if missing)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    print("Warning: OPENAI_API_KEY not set. OpenAI requests will fail until a key is provided.")
openai.api_key = OPENAI_API_KEY

# S3 configuration: default to the bucket and prefix you specified
S3_BUCKET = os.getenv("S3_BUCKET", "ocr-files-db")
# ensure prefix ends with slash if provided
S3_PREFIX = os.getenv("S3_PREFIX", "pdf/")
if S3_PREFIX and not S3_PREFIX.endswith("/"):
    S3_PREFIX = S3_PREFIX + "/"

# Create S3 client (uses standard boto3 env/auth configuration)
s3_client = boto3.client("s3")

engine = create_engine(DB_URL)
app = FastAPI(title="Reports Service (Local)")

# ==================== MODELOS ====================
class Report(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tenant_id: uuid.UUID
    type: str
    period: str
    params: dict = Field(sa_column=Column(JSON))
    status: str = Field(default="processing")
    storage_key_pdf: str | None = None
    storage_key_tex: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

SQLModel.metadata.create_all(engine)

class ReportRequest(BaseModel):
    tenant_id: uuid.UUID
    type: str
    period: str
    params: dict
    ai_prompt: str | None = "Genera un documento en LaTeX que resuma los datos financieros proporcionados."

# ==================== UTILIDAD ====================
def sanitize_latex(text: str) -> str:
    """Escapa caracteres especiales solo en texto plano, sin alterar comandos LaTeX."""
    if not isinstance(text, str):
        text = str(text)
    replacements = {
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    for char, escaped in replacements.items():
        text = re.sub(rf"(?<!\\){re.escape(char)}", escaped, text)
    return text

# ==================== IA: GENERAR LATEX ====================
def generate_latex_from_ai(params: dict, prompt: str) -> str:
    """Genera un documento LaTeX completo y válido."""
    system_prompt = (
        "Eres un generador experto de reportes en LaTeX. "
        "Tu salida debe ser un documento completo, comenzando con \\documentclass y "
        "terminando con \\end{document}. Sin Markdown ni explicaciones."
    )

    user_prompt = f"""
Instrucción: {prompt}

Datos del reporte:
{json.dumps(params, indent=2, ensure_ascii=False)}

Genera un documento LaTeX completo en español, con estructura mínima profesional:
- \\documentclass{{article}}
- \\usepackage[utf8]{{inputenc}}
- \\usepackage{{booktabs}}
- \\begin{{document}} ... \\end{{document}}
Incluye título, resumen, tabla con resultados y conclusión.
"""

    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )

    latex_output = response.choices[0].message.content.strip()
    latex_output = re.sub(r"^```latex|```$", "", latex_output, flags=re.MULTILINE).strip()

    # Si no tiene estructura base, usa plantilla por defecto
    if not latex_output.lstrip().startswith("\\documentclass"):
        ventas = sanitize_latex(params.get("ventas", "N/A"))
        costos = sanitize_latex(params.get("costos", "N/A"))
        utilidad = sanitize_latex(params.get("utilidad", "N/A"))
        comentario = sanitize_latex(params.get("comentario", "Sin comentarios."))

        latex_output = rf"""
\documentclass[12pt]{{article}}
\usepackage[utf8]{{inputenc}}
\usepackage{{booktabs}}
\usepackage{{geometry}}
\geometry{{margin=1in}}
\begin{{document}}
\section*{{Reporte Financiero}}
Generado automáticamente a partir de los siguientes datos:
\begin{{itemize}}
\item Ventas: {ventas}
\item Costos: {costos}
\item Utilidad: {utilidad}
\end{{itemize}}

Comentario: {comentario}
\end{{document}}
""".strip()

    # NO sanitizamos el documento completo (ya está escapado correctamente)
    return latex_output

# ==================== COMPILACIÓN ====================
def upload_file_to_s3(local_path: str, bucket: str, key: str) -> str:
    """Upload a local file to S3 and return the s3:// path.

    Raises the underlying boto3 exception on failure.
    """
    s3_client.upload_file(local_path, bucket, key)
    return f"s3://{bucket}/{key}"


def compile_latex_to_pdf(tex_content: str, report_id: uuid.UUID, tenant_id: uuid.UUID) -> tuple[str, str]:
    """Guarda el contenido LaTeX, genera el PDF localmente y sube ambos a S3.

    Returns (s3_tex_path, s3_pdf_path) as strings (s3://...)
    Also keeps a local copy under OUTPUT_DIR for convenience.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = Path(tmpdir) / f"{report_id}.tex"
        pdf_path = Path(tmpdir) / f"{report_id}.pdf"
        tex_path.write_text(tex_content, encoding="utf-8")

        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", tex_path.name],
            cwd=tmpdir,
            capture_output=True,
            text=True
        )

        if not pdf_path.exists():
            raise RuntimeError(
                f"LaTeX no produjo el PDF.\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
            )

        # Local final copies
        final_tex = OUTPUT_DIR / f"{report_id}.tex"
        final_pdf = OUTPUT_DIR / f"{report_id}.pdf"
        tex_path.replace(final_tex)
        pdf_path.replace(final_pdf)

        # Build S3 keys: prefix / tenant_id / filename
        tenant_folder = f"{tenant_id}"
        s3_tex_key = f"{S3_PREFIX}{tenant_folder}/{report_id}.tex"
        s3_pdf_key = f"{S3_PREFIX}{tenant_folder}/{report_id}.pdf"

        # Upload to S3
        try:
            s3_tex_path = upload_file_to_s3(str(final_tex), S3_BUCKET, s3_tex_key)
            s3_pdf_path = upload_file_to_s3(str(final_pdf), S3_BUCKET, s3_pdf_key)
        except Exception as e:
            # Keep local copies for debugging, but surface the error upstream
            raise RuntimeError(f"Error subiendo archivos a S3: {e}") from e

    return s3_tex_path, s3_pdf_path

# ==================== ENDPOINTS ====================
@app.post("/reports/generate")
def generate_report(req: ReportRequest):
    """Genera un nuevo reporte y lo guarda en local."""
    with Session(engine) as session:
        report = Report(
            tenant_id=req.tenant_id,
            type=req.type,
            period=req.period,
            params=req.params,
        )
        session.add(report)
        session.commit()
        session.refresh(report)

    try:
        # 🔹 Generar LaTeX con GPT
        latex = generate_latex_from_ai(req.params, req.ai_prompt)

        # ⚙️ Compilar a PDF (ahora sube a S3)
        tex_path, pdf_path = compile_latex_to_pdf(latex, report.id, report.tenant_id)

        # 🧾 Actualizar estado en base de datos
        with Session(engine) as session:
            report = session.get(Report, report.id)
            report.status = "ready"
            report.storage_key_tex = tex_path
            report.storage_key_pdf = pdf_path
            report.updated_at = datetime.utcnow()
            session.add(report)
            session.commit()
            report_id = str(report.id)  # ✅ Guardamos antes de cerrar sesión

        # ✅ Respuesta final
        return {
            "id": report_id,
            "status": "ready",
            "pdf_path": pdf_path,
        }

    except Exception as e:
        # ❌ Manejo de errores y actualización de estado
        with Session(engine) as session:
            report = session.get(Report, report.id)
            report.status = "error"
            report.updated_at = datetime.utcnow()
            session.add(report)
            session.commit()
        raise HTTPException(status_code=500, detail=f"Error generando reporte: {e}")


@app.get("/reports/{report_id}")
def get_report(report_id: uuid.UUID):
    with Session(engine) as session:
        report = session.exec(select(Report).where(Report.id == report_id)).first()
        if not report:
            raise HTTPException(status_code=404, detail="Reporte no encontrado")
        return report

@app.get("/reports/download/{report_id}")
def download_report(report_id: uuid.UUID):
    with Session(engine) as session:
        report = session.exec(select(Report).where(Report.id == report_id)).first()
        if not report or not report.storage_key_pdf:
            raise HTTPException(status_code=404, detail="PDF no disponible")
        return FileResponse(report.storage_key_pdf, filename=f"{report_id}.pdf")

@app.get("/healthz")
def health():
    return {"status": "ok", "output_dir": str(OUTPUT_DIR)}
