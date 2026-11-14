from fastapi import FastAPI

app = FastAPI(title="Switchboard")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
