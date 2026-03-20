"""
main.py - archi_agent CLI 진입점

사용법:
    python -m archi_agent.main --ifc <IFC파일경로> --request "<자연어요청>" [--clear]

예시:
    python -m archi_agent.main \
        --ifc raw/Ifc4_SampleHouse_IfcWallStandardCase.ifc \
        --request "모든 벽의 이름을 조회해서 보고해줘"
"""
import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ── 프로젝트 루트를 sys.path에 추가 (직접 실행 대응) ──────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from db.neo4j_client import Neo4jClient
from db.graph_initializer import GraphInitializer
from graph.orchestrator import build_graph
from graph.state import AgentState


# ──────────────────────────────────────────────────────────────────────────────
# 로깅 설정
# ──────────────────────────────────────────────────────────────────────────────

def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ──────────────────────────────────────────────────────────────────────────────
# 환경변수 로드
# ──────────────────────────────────────────────────────────────────────────────

def load_env() -> dict:
    """archigent/.env 파일을 로드하고 필수 변수를 반환합니다."""
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        logging.getLogger(__name__).info(f".env 로드 완료: {env_path}")
    else:
        logging.getLogger(__name__).warning(f".env 파일 없음: {env_path}")

    required = ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "GOOGLE_API_KEY"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        print(f"[오류] 필수 환경변수 누락: {', '.join(missing)}")
        print(f"       .env.example을 참고하여 {env_path}를 생성하세요.")
        sys.exit(1)

    return {
        "neo4j_uri":      os.getenv("NEO4J_URI"),
        "neo4j_user":     os.getenv("NEO4J_USER"),
        "neo4j_password": os.getenv("NEO4J_PASSWORD"),
        "neo4j_database": os.getenv("NEO4J_DATABASE", "neo4j"),
        "google_api_key": os.getenv("GOOGLE_API_KEY"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="archi_agent - IFC Agentic Workflow (LangGraph + Gemini)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ifc",     required=False, help="원본 IFC 파일 경로 (생략 시 목록에서 선택)")
    parser.add_argument("--request", required=True,  help="수행할 작업 (자연어)")
    parser.add_argument("--output",  default="",     help="출력 IFC 파일 경로 (기본: output_<원본파일명>)")
    parser.add_argument("--clear",   action="store_true", help="실행 전 Neo4j DB 초기화")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG","INFO","WARNING","ERROR"])
    parser.add_argument("--skip-init", action="store_true", help="Neo4j 초기화 단계 건너뜀 (이미 로드된 경우)")
    return parser.parse_args()


# ──────────────────────────────────────────────────────────────────────────────
# 메인 실행
# ──────────────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    print("\n" + "="*60)
    print("  archi_agent  |  IFC Agentic Workflow")
    print("="*60)

    # 1. 환경변수 로드
    env = load_env()

    # 2. Neo4j 연결
    client = Neo4jClient(
        uri=env["neo4j_uri"],
        user=env["neo4j_user"],
        password=env["neo4j_password"],
        database=env["neo4j_database"],
    )
    if not client.connect():
        print("[오류] Neo4j 연결 실패. DB가 실행 중인지 확인하세요.")
        sys.exit(1)
    print("✅ Neo4j 연결 성공")

    # 3. IFC 파일 경로 결정 및 초기화
    ifc_path_str = args.ifc
    if not ifc_path_str:
        # raw 폴더에서 파일 목록 나열 및 선택
        raw_dir = ROOT / "raw"
        ifc_files = list(raw_dir.glob("*.ifc"))
        if not ifc_files:
            print("[오류] raw 폴더에 IFC 파일이 없습니다.")
            sys.exit(1)
        
        print("\n📂 분석할 IFC 파일을 선택해 주세요:")
        for i, f in enumerate(ifc_files):
            print(f" [{i}] {f.name}")
        
        choice = input(f"파일 번호 선택 (0-{len(ifc_files)-1}): ")
        idx = int(choice) if choice.isdigit() and 0 <= int(choice) < len(ifc_files) else 0
        ifc_path = ifc_files[idx]
    else:
        ifc_path = Path(ifc_path_str)

    if not ifc_path.exists():
        print(f"[오류] IFC 파일을 찾을 수 없습니다: {ifc_path}")
        sys.exit(1)

    if not args.skip_init:
        print(f"\n📦 IFC 파일 로드 중: {ifc_path.name}")
        initializer = GraphInitializer(client)
        init_result = initializer.initialize(ifc_path, clear_first=args.clear)

        if not init_result["success"]:
            print(f"[오류] DB 초기화 실패: {init_result.get('error')}")
            sys.exit(1)

        stats = init_result.get("db_stats", {})
        print(f"✅ DB 초기화 완료 — 노드: {stats.get('total_nodes', '?')}, 관계: {stats.get('total_relationships', '?')}")
    else:
        print("⏭️  DB 초기화 단계 건너뜀 (--skip-init)")

    # 4. LangGraph 파이프라인 실행
    output_dir = ROOT / "modified"
    output_dir.mkdir(exist_ok=True)
    output_path = args.output or str(output_dir / f"mod_{ifc_path.name}")
    
    # [Rollback Readiness] 원본 백업
    backup_dir = Path("backups")
    backup_dir.mkdir(exist_ok=True)
    import shutil
    backup_path = backup_dir / f"orig_{ifc_path.name}"
    shutil.copy(ifc_path, backup_path)

    print(f"\n🤖 에이전트 파이프라인 시작")
    print(f"   요청: {args.request}")
    print(f"   출력: {output_path}")
    print(f"   백업: {backup_path}\n")

    initial_state: AgentState = {
        "user_request":    args.request,
        "ifc_path":        str(ifc_path.absolute()),
        "output_ifc_path": output_path,
        "original_ifc_path": str(backup_path.absolute()),
        "iteration":       0,
        "error":           None,
        "version_history": [str(ifc_path.absolute())]
    }

    # 인터럽트 관리를 위한 체크포인터
    from langgraph.checkpoint.memory import MemorySaver
    memory = MemorySaver()
    
    graph = build_graph(checkpointer=memory)
    
    config = {
        "configurable": {
            "thread_id": "archi_user_1",
            "neo4j_client": client
        }
    }

    try:
        # [HITL Step 1] Planner까지 실행 후 Interruption
        print("💡 시안 생성 중... 선택 단계에서 잠시 멈춥니다.")
        for event in graph.stream(initial_state, config=config, stream_mode="values"):
            pass
        
        # 중단된 지점이 selection_node 전이라면 사용자 선택 유도
        snapshot = graph.get_state(config)
        if snapshot.next:
            options = snapshot.values.get("plan_options", [])
            
            if len(options) > 1:
                # 시안 목록 출력
                print("\n" + "-"*30)
                print("🏗️ 다음 지시사항 중 하나를 선택하세요:")
                for i, opt in enumerate(options):
                    print(f" [{i}] {opt.get('title')}: {opt.get('task_spec')[:100]}...")
                
                print("-"*30)
                choice = input(f"인덱스 선택 (0-{len(options)-1}): ")
                idx = int(choice) if choice.isdigit() else 0
            else:
                # 단일 시안(Query 등)인 경우 자동 선택
                idx = 0
                title = options[0].get('title') if options else "기본 시안"
                print(f"\n✅ 단일 시안('{title}')으로 자동 진행합니다.\n")
            
            # 상태 업데이트 후 재개
            graph.update_state(config, {"selected_option_index": idx})
            
            # 파이프라인 재개
            final_state = graph.invoke(None, config=config)
        else:
            final_state = snapshot.values

    except Exception as exc:
        logger.error(f"파이프라인 실행 중 오류: {exc}")
        import traceback
        traceback.print_exc()
        print(f"\n[오류] 파이프라인 실패: {exc}")
        sys.exit(1)

    # 5. 결과 출력
    print("\n" + "="*60)
    print("  최종 결과")
    print("="*60)
    
    # 분석 보고서가 있다면 출력
    if final_state.get("graph_summary"):
        print("\n📊 분석 보고서:")
        print(final_state.get("graph_summary"))

    print(f"\n✅ 검증 결과: {final_state.get('verification_result', '없음')}")

    if Path(output_path).exists():
        print(f"\n💾 출력 파일 저장 완료: {output_path}")

    client.close()


if __name__ == "__main__":
    main()
