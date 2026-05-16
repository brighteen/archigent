import os
import shutil
import logging
import asyncio
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
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
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

STATIC_DIR = ROOT / "static"
STATIC_DIR.mkdir(exist_ok=True)
(ROOT / "raw").mkdir(exist_ok=True)
(ROOT / "modified").mkdir(exist_ok=True)
(ROOT / "proposals").mkdir(exist_ok=True)
(ROOT / "archive").mkdir(exist_ok=True)
(ROOT / "backups").mkdir(exist_ok=True)

# ── FastAPI 앱 ─────────────────────────────────────────────────────────
app = FastAPI(title="ArchiGent API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/api/ifc/raw", StaticFiles(directory=str(ROOT / "raw")), name="ifc_raw")
app.mount("/api/ifc/modified", StaticFiles(directory=str(ROOT / "modified")), name="ifc_modified")
app.mount("/api/ifc/proposals", StaticFiles(directory=str(ROOT / "proposals")), name="ifc_proposals")

# ── 전역 상태 (멀티유저 대응) ──────────────────────────────────────────────
class AppState:
    def __init__(self):
        self.neo4j_client = None
        self.memory = MemorySaver()
        # taskId -> { "logs": [], "is_running": bool, "status": str, "created_at": datetime, ... }
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.last_cleanup = datetime.now()

    def cleanup_old_tasks(self, max_age_hours: int = 12):
        """만료된(12시간 이상된) 작업 데이터를 정리하여 메모리를 관리합니다."""
        now = datetime.now()
        expired = [
            tid for tid, task in self.tasks.items()
            if (now - task.get("created_at", now)).total_seconds() > max_age_hours * 3600
               and not task.get("is_running", False)
        ]
        for tid in expired:
            del self.tasks[tid]
        if expired:
            logger.info(f"💾 TaskStore: {len(expired)}개의 오래된 태스크를 메모리에서 정리했습니다.")

state = AppState()

class GenerateRequest(BaseModel):
    ifc_filename: str
    user_request: str

class SelectionRequest(BaseModel):
    index: int
    overrides: Optional[Dict[str, Any]] = None

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
    task_id = str(uuid.uuid4())
    ifc_path = ROOT / "raw" / req.ifc_filename
    if not ifc_path.exists():
        raise HTTPException(status_code=404, detail="IFC 파일 없음")
    
    # 주기적 정리 수행 (1시간 간격 체크)
    if (datetime.now() - state.last_cleanup).total_seconds() > 3600:
        state.cleanup_old_tasks()
        state.last_cleanup = datetime.now()

    # 태스크 초기화
    state.tasks[task_id] = {
        "logs": [],
        "is_running": True,
        "status": "starting",
        "result": None,
        "ifc_filename": req.ifc_filename,
        "created_at": datetime.now()
    }
    
    background_tasks.add_task(run_agent_pipeline, task_id, req.ifc_filename, req.user_request)
    return {"task_id": task_id}

@app.get("/api/events")
async def event_stream(task_id: str):
    if task_id not in state.tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    async def log_generator():
        last_idx = 0
        while True:
            task = state.tasks.get(task_id)
            if not task:
                yield "data: [DONE]\n\n"
                break
            
            if last_idx < len(task["logs"]):
                for i in range(last_idx, len(task["logs"])):
                    yield f"data: {task['logs'][i]}\n\n"
                last_idx = len(task["logs"])
            
            if not task["is_running"] and last_idx >= len(task["logs"]):
                yield "data: [DONE]\n\n"
                break
                
            await asyncio.sleep(0.3)
    return StreamingResponse(log_generator(), media_type="text/event-stream")

class SelectionRequest(BaseModel):
    task_id: str
    index: int
    overrides: Optional[Dict[str, Any]] = None

@app.post("/api/select")
async def select_option(req: SelectionRequest):
    if req.task_id not in state.tasks:
        raise HTTPException(status_code=404, detail="Task not found")
        
    task = state.tasks[req.task_id]
    config = {"configurable": {"thread_id": req.task_id, "neo4j_client": get_neo4j_client()}}
    
    graph = build_graph(checkpointer=state.memory)
# ── 에이전트 파이프라인 ─────────────────────────────────────────────────

async def run_agent_pipeline(task_id: str, ifc_filename: str, user_request: str):
    task = state.tasks[task_id]
    try:
        client = get_neo4j_client()
        ifc_path = ROOT / "raw" / ifc_filename
        # 결과 파일명에도 taskId를 포함하여 충돌 방지
        output_path = ROOT / "modified" / f"mod_{task_id[:8]}_{ifc_filename}"
        backup_path = ROOT / "backups" / f"orig_{ifc_filename}_{task_id[:8]}"
        shutil.copy(ifc_path, backup_path)

        task["logs"].append(f"LOG: IFC 초기화 중... ({ifc_filename})")
        GraphInitializer(client).initialize(ifc_path, task_id, clear_first=True)

        # 체크포인터 복구
        graph = build_graph(checkpointer=state.memory)
        initial: AgentState = {
            "task_id": task_id,
            "user_request": user_request,
            "ifc_path": str(ifc_path.absolute()),
            "output_ifc_path": str(output_path.absolute()),
            "original_ifc_path": str(backup_path.absolute()),
            "iteration": 0,
            "version_history": [str(ifc_path.absolute())]
        }
        config = {"configurable": {"thread_id": task_id, "neo4j_client": client}}

        # updates 모드를 사용하여 각 노드의 실행을 직접 감지
        async for event in graph.astream(initial, config=config, stream_mode="updates"):
            # updates 모드에서 event는 { "node_name": { "updated_field": "value" } } 형태입니다.
            node_name = list(event.keys())[0] if event else "unknown"
            node_data = event.get(node_name, {})
            
            logger.info(f"[Pipeline Event] Node: {node_name}")
            task["logs"].append(f"STATUS:{node_name}")
            
            if node_name == "starter":
                task["logs"].append("AUDIT:INFO|ArchiGent 파이프라인 엔진 시동 완료")
            elif node_name == "analyzer":
                task["logs"].append("AUDIT:INFO|IFC 데이터 및 사용자 요청 분석 완료")
            elif node_name == "planner":
                task["logs"].append("AUDIT:INFO|설계 최적화 및 작업 명세 생성 완료")
            elif node_name == "coder":
                if node_data.get("iteration_success"):
                    task["logs"].append("AUDIT:SUCCESS|IFC 기하학 연산 및 코드 실행 성공")
                else:
                    task["logs"].append("AUDIT:WARNING|코드 실행 중 오류 발생 (교정 시도 중...)")
            elif node_name == "verifier":
                task["logs"].append(f"AUDIT:INFO|검증 결과: {node_data.get('verification_result', 'N/A')}")

            # 최종 결과물 체크 및 응답 스트리밍
            output_ifc = node_data.get("output_ifc_path") or initial["output_ifc_path"]
            
            if node_name == "verifier" and Path(output_ifc).exists() and node_data.get("verification_result", "").startswith("PASS"):
                task["logs"].append(f"RESULT:{Path(output_ifc).name}")
                task["logs"].append("AUDIT:SUCCESS|모델 검증 성공. 최종 응답 생성 중...")
                
            elif node_name == "responder":
                final_resp = node_data.get("final_chat_response", "요청하신 작업이 완료되었습니다.")
                task["logs"].append("AUDIT:INFO|대화형 응답 출력 중...")
                # 한 글자씩은 너무 비효율적이므로 5글자씩 묶어서 전송
                chunk_size = 5
                for i in range(0, len(final_resp), chunk_size):
                    chunk = final_resp[i:i + chunk_size]
                    escaped_chunk = chunk.replace('\n', '\\n')
                    task["logs"].append(f"RESPONSE:{escaped_chunk}")
                    await asyncio.sleep(0.02)
                
                if node_data.get("verification_result", "").startswith("PASS"):
                    task["logs"].append("AUDIT:SUCCESS|모든 파이프라인이 성공적으로 완료되었습니다.")
                else:
                    task["logs"].append("AUDIT:ERROR|파이프라인 실행 실패. 응답을 확인해주세요.")
                return

        # 루프 종료 후에도 결과가 없는 경우
        if not task.get("logs") or not any("RESULT:" in l for l in task["logs"]):
             task["logs"].append("ERROR: 파이프라인이 결과 파일 생성 없이 종료되었습니다. 로그를 확인해 주세요.")

    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        task["logs"].append(f"ERROR: {str(e)}")
    finally:
        task["is_running"] = False

async def resume_agent_pipeline(task_id: str):
    task = state.tasks[task_id]
    task["is_running"] = True
    try:
        config = {"configurable": {"thread_id": task_id, "neo4j_client": get_neo4j_client()}}
        graph = build_graph(checkpointer=state.memory)
        async for event in graph.astream(None, config=config, stream_mode="values"):
            snapshot = await graph.aget_state(config)
            node_name = snapshot.next[0] if snapshot.next else "end"
            task["logs"].append(f"STATUS:{node_name}")
            
            if "output_ifc_path" in event and node_name == "end":
                output_path = event.get("output_ifc_path")
                task["logs"].append(f"RESULT:{Path(output_path).name}")
                task["logs"].append("AUDIT:SUCCESS|정밀 수정 및 검증 완료")
        
        task["logs"].append("LOG: 작업 완료!")
    except Exception as e:
        task["logs"].append(f"ERROR: {str(e)}")
    finally:
        task["is_running"] = False

if __name__ == "__main__":
    logger.info("🚀 ArchiGent 서버 시작 — http://localhost:8001")
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
