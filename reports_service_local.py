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

from sqlmodel import SQLModel, Field, create_engine, Session, select
import openai
load_dotenv()
# ============ CONFIG LOCAL ============
DB_URL = "sqlite:///./reports_local.db"
OUTPUT_DIR = Path(os.getenv("REPORTS_OUTPUT_DIR", "./output_reports"))
OUTPUT_DIR.mkdir(exist_ok=True)
openai.api_key = os.getenv("OPENAI_API_KEY")  # Coloca tu API key en .env

engine = create_engine(DB_URL)
app = FastAPI(title="Reports Service (Local)")

# ============ MODELO ============
class Report(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tenant_id: uuid.UUID
    type: str
    period: str
    params: dict = Field(sa_column=Column(JSON))  # ✅ Tipo JSON nativo
    status: str = Field(default="processing")
    storage_key_pdf: str | None = None
    storage_key_tex: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


SQLModel.metadata.create_all(engine)

# ============ MODELOS DE REQUEST ============
class ReportRequest(BaseModel):
    tenant_id: uuid.UUID
    type: str
    period: str
    params: dict
    ai_prompt: str | None = "Genera un documento en LaTeX que resuma los datos financieros proporcionados."

# ============ FUNCIÓN IA ============
def generate_latex_from_ai(params: dict, prompt: str) -> str:
    """Genera un documento LaTeX usando OpenAI GPT."""
    content = f"{prompt}\n\nParámetros:\n{json.dumps(params, indent=2, ensure_ascii=False)}"
    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Eres un generador experto de documentos técnicos en LaTeX."},
            {"role": "user", "content": content},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content

# ============ FUNCIÓN COMPILACIÓN ============
def compile_latex_to_pdf(tex_content: str, report_id: uuid.UUID) -> tuple[str, str]:
    """Guarda el contenido LaTeX y genera el PDF localmente."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = Path(tmpdir) / f"{report_id}.tex"
        pdf_path = Path(tmpdir) / f"{report_id}.pdf"
        tex_path.write_text(tex_content, encoding="utf-8")

        # Compilar con pdflatex
        subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", tex_path.name],
            cwd=tmpdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )

        # Guardar en la carpeta local definitiva
        final_tex = OUTPUT_DIR / f"{report_id}.tex"
        final_pdf = OUTPUT_DIR / f"{report_id}.pdf"
        tex_path.replace(final_tex)
        pdf_path.replace(final_pdf)

    return str(final_tex), str(final_pdf)

# ============ ENDPOINTS ============

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
        latex = generate_latex_from_ai(req.params, req.ai_prompt)
        tex_path, pdf_path = compile_latex_to_pdf(latex, report.id)

        with Session(engine) as session:
            report = session.get(Report, report.id)
            report.status = "ready"
            report.storage_key_tex = tex_path
            report.storage_key_pdf = pdf_path
            report.updated_at = datetime.utcnow()
            session.add(report)
            session.commit()

        return {"id": str(report.id), "status": "ready", "pdf_path": pdf_path}

    except Exception as e:
        with Session(engine) as session:
            report = session.get(Report, report.id)
            report.status = "error"
            session.add(report)
            session.commit()
        raise HTTPException(status_code=500, detail=f"Error generando reporte: {e}")

@app.get("/reports/{report_id}")
def get_report(report_id: uuid.UUID):
    """Devuelve metadatos del reporte."""
    with Session(engine) as session:
        report = session.exec(select(Report).where(Report.id == report_id)).first()
        if not report:
            raise HTTPException(status_code=404, detail="Reporte no encontrado")
        return report

@app.get("/reports/download/{report_id}")
def download_report(report_id: uuid.UUID):
    """Descarga el PDF generado."""
    with Session(engine) as session:
        report = session.exec(select(Report).where(Report.id == report_id)).first()
        if not report or not report.storage_key_pdf:
            raise HTTPException(status_code=404, detail="PDF no disponible")
        return FileResponse(report.storage_key_pdf, filename=f"{report_id}.pdf")

@app.get("/healthz")
def health():
    return {"status": "ok", "output_dir": str(OUTPUT_DIR)}
