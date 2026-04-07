from fastapi import FastAPI

from routers import cim

app = FastAPI(title="CIM API")

app.include_router(cim.router)
