import os
import shutil
import logging
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv

from db.neo4j_client import Neo4jClient
from db.graph_initializer import GraphInitializer
from graph.orchestrator import build_graph
from graph.state import AgentState
from langgraph.checkpoint.memory import MemorySaver

# ── 기본 설정 ──────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(ROOT / "server_debug.log"), encoding="utf-8")
    ]
)
logger = logging.getLogger("archigent.server")

STATIC_DIR = ROOT / "static"
STATIC_DIR.mkdir(exist_ok=True)
(ROOT / "raw").mkdir(exist_ok=True)
(ROOT / "modified").mkdir(exist_ok=True)
(ROOT / "backups").mkdir(exist_ok=True)

# ── FastAPI 앱 ─────────────────────────────────────────────────────────
app = FastAPI(title="ArchiGent API")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── 전역 상태 ──────────────────────────────────────────────────────────
class AppState:
    def __init__(self):
        self.neo4j_client = None
        self.memory = MemorySaver()
        self.thread_id = "web_user_1"
        self.logs: List[str] = []
        self.is_running = False

state = AppState()

class GenerateRequest(BaseModel):
    ifc_filename: str
    user_request: str

class SelectionRequest(BaseModel):
    index: int

# ── 유틸리티 ────────────────────────────────────────────────────────────
def get_neo4j_client():
    if not state.neo4j_client:
        state.neo4j_client = Neo4jClient(
            uri=os.getenv("NEO4J_URI"),
            user=os.getenv("NEO4J_USER"),
            password=os.getenv("NEO4J_PASSWORD"),
            database=os.getenv("NEO4J_DATABASE", "neo4j")
        )
        if not state.neo4j_client.connect():
            logger.warning("⚠️ Neo4j 연결 실패 (Docker를 켜셨나요?)")
    return state.neo4j_client

# ── 엔드포인트 ──────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}

@app.post("/api/log")
async def remote_log(req: Dict[str, Any]):
    logger.info(f"🌐 [BROWSER] {req.get('message', '')}")
    return {"ok": True}

@app.get("/{path:path}web-ifc.wasm")
@app.get("/web-ifc.wasm")
async def wasm_serve(path: str = ""):
    """어떤 오염된 경로에서도 web-ifc.wasm을 강제로 반환"""
    wasm_path = STATIC_DIR / "web-ifc.wasm"
    if wasm_path.exists():
        return FileResponse(wasm_path, media_type="application/wasm")
    return Response(status_code=404)

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/api/files")
async def list_files():
    files = [f.name for f in (ROOT / "raw").glob("*.ifc")]
    return {"files": files}

@app.get("/api/ifc/{folder}/{filename}")
async def get_ifc_file(folder: str, filename: str):
    path = ROOT / folder / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="파일 없음")
    return FileResponse(path, media_type="application/octet-stream")

@app.post("/api/generate")
async def start_generation(req: GenerateRequest, background_tasks: BackgroundTasks):
    if state.is_running:
        return JSONResponse({"status": "error", "message": "이미 실행 중"}, status_code=400)
    state.logs = []
    state.is_running = True
    ifc_path = ROOT / "raw" / req.ifc_filename
    if not ifc_path.exists():
        state.is_running = False
        raise HTTPException(status_code=404, detail="IFC 파일 없음")
    background_tasks.add_task(run_agent_pipeline, req.ifc_filename, req.user_request)
    return {"status": "started"}

@app.get("/api/events")
async def event_stream():
    async def log_generator():
        last_idx = 0
        while state.is_running or last_idx < len(state.logs):
            if last_idx < len(state.logs):
                for i in range(last_idx, len(state.logs)):
                    yield f"data: {state.logs[i]}\n\n"
                last_idx = len(state.logs)
            await asyncio.sleep(0.5)
            if not state.is_running and last_idx >= len(state.logs):
                yield "data: [DONE]\n\n"
                break
    return StreamingResponse(log_generator(), media_type="text/event-stream")

@app.post("/api/select")
async def select_option(req: SelectionRequest):
    config = {"configurable": {"thread_id": state.thread_id, "neo4j_client": get_neo4j_client()}}
    build_graph().update_state(config, {"selected_option_index": req.index})
    asyncio.create_task(resume_agent_pipeline())
    return {"status": "selected"}

# ── 에이전트 파이프라인 ─────────────────────────────────────────────────

async def run_agent_pipeline(ifc_filename: str, user_request: str):
    try:
        client = get_neo4j_client()
        ifc_path = ROOT / "raw" / ifc_filename
        output_path = ROOT / "modified" / f"mod_{ifc_filename}"
        backup_path = ROOT / "backups" / f"orig_{ifc_filename}"
        shutil.copy(ifc_path, backup_path)

        state.logs.append(f"LOG: IFC 초기화 중... ({ifc_filename})")
        GraphInitializer(client).initialize(ifc_path, clear_first=True)

        state.logs.append("LOG: 에이전트 파이프라인 시작")
        graph = build_graph(checkpointer=state.memory)
        initial: AgentState = {
            "user_request": user_request,
            "ifc_path": str(ifc_path.absolute()),
            "output_ifc_path": str(output_path.absolute()),
            "original_ifc_path": str(backup_path.absolute()),
            "iteration": 0,
            "version_history": [str(ifc_path.absolute())]
        }
        config = {"configurable": {"thread_id": state.thread_id, "neo4j_client": client}}

        async for event in graph.astream(initial, config=config, stream_mode="values"):
            snapshot = await graph.aget_state(config)
            node_name = snapshot.next[0] if snapshot.next else "end"
            state.logs.append(f"STATUS:{node_name}")
            if "plan_options" in event and node_name == "selection":
                opts = "|".join([opt.get("title", "시안") for opt in event["plan_options"]])
                state.logs.append(f"OPTIONS:{opts}")
                state.is_running = False
                return

        state.logs.append("LOG: 작업 완료!")
        state.logs.append(f"RESULT:mod_{ifc_filename}")
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        state.logs.append(f"ERROR: {str(e)}")
    finally:
        state.is_running = False

async def resume_agent_pipeline():
    state.is_running = True
    try:
        config = {"configurable": {"thread_id": state.thread_id, "neo4j_client": get_neo4j_client()}}
        graph = build_graph(checkpointer=state.memory)
        async for event in graph.astream(None, config=config, stream_mode="values"):
            snapshot = await graph.aget_state(config)
            node_name = snapshot.next[0] if snapshot.next else "end"
            state.logs.append(f"STATUS:{node_name}")
        snapshot = await graph.aget_state(config)
        output_path = Path(snapshot.values.get("output_ifc_path", ""))
        state.logs.append("LOG: 작업 완료!")
        state.logs.append(f"RESULT:{output_path.name}")
    except Exception as e:
        state.logs.append(f"ERROR: {str(e)}")
    finally:
        state.is_running = False

if __name__ == "__main__":
    logger.info("🚀 ArchiGent 서버 시작 — http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
