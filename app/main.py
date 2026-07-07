from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Interview Agent MVP")
app.include_router(router)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def root():
    return FileResponse(BASE_DIR / "test4.html")


@app.get("/prep")
def prep_page():
    return FileResponse(BASE_DIR / "test4.html")


@app.get("/interview")
def interview_page():
    return FileResponse(BASE_DIR / "test3.html")


@app.get("/report-processing")
def report_processing_page():
    return FileResponse(BASE_DIR / "test2.html")


@app.get("/report-detail")
def report_detail_page():
    return FileResponse(BASE_DIR / "test1.html")


@app.get("/reports")
def reports_page():
    return FileResponse(BASE_DIR / "test0.html")
