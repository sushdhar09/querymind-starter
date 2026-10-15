import uvicorn

if __name__ == "__main__":
    # reload=False is important for debugging
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)