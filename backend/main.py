from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.generate import router as generate_router

app = FastAPI(title="Rork Backend Bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(generate_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
