"""Compatibility redirects for website clients using the retired API address."""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

TARGET = "https://wli14-histagent-agent.hf.space"
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://histagent.bio", "https://www.histagent.bio"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-HistAgent-Session"],
)


@app.get("/api/health")
@app.post("/api/generate")
@app.post("/api/call")
def redirect_api(request: Request):
    # Browser follows this redirect directly; the old Space makes no model call.
    return RedirectResponse(TARGET + request.url.path, status_code=307)


@app.get("/")
def homepage():
    return RedirectResponse("https://histagent.bio", status_code=307)
