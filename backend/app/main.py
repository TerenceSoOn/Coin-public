import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from .api.router import api_router
from .scheduler import start_scheduler
from .db.database import init_db

app = FastAPI(title="OKX Spot AI Trader")

# Enable CORS for frontend UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to specific domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def on_startup():
    # Initialize database tables
    init_db()
    # Start scheduler (1m fetch, 10m AI, 3% trigger configurable)
    start_scheduler()

# Include API router FIRST
app.include_router(api_router, prefix="/api")

# Serve frontend static files
frontend_path = os.path.join(os.path.dirname(__file__), '../../frontend')

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Serve index.html for root path"""
    index_path = os.path.join(frontend_path, 'index.html')
    if os.path.exists(index_path):
        with open(index_path, 'r') as f:
            return f.read()
    return "<html><body>Frontend not found</body></html>"

@app.get("/{file_path:path}", name="static")
async def serve_static(file_path: str):
    """Serve static files (css, js, etc.) - skip /api routes"""
    # Don't intercept API routes or health check
    if file_path.startswith('api/') or file_path.startswith('api'):
        return {"detail": "Not Found"}
    
    # Serve files from frontend directory
    full_path = os.path.join(frontend_path, file_path)
    if os.path.exists(full_path) and os.path.isfile(full_path):
        return FileResponse(full_path)
    
    # For unknown routes, serve index.html (SPA behavior)
    index_path = os.path.join(frontend_path, 'index.html')
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
