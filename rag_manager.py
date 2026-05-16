import os
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

class RAGManager:
    """
    고도화된 RAG 매니저: 
    단순 파일 읽기 방식에서 TF-IDF 기반의 랭킹 검색 방식으로 전환하여
    대량의 문서 중 가장 관련성 높은 정보만 선별해 제공합니다.
    """
    
    def __init__(self, root_dir: Optional[Path] = None):
        self.root_dir = root_dir or Path(__file__).parent
        self.reg_path = self.root_dir / "regulations"
        self.code_path = self.root_dir / "prompts" / "code_samples"
        
        # 캐싱된 인덱스
        self.reg_docs: List[Dict[str, str]] = []
        self.code_docs: List[Dict[str, str]] = []
        
        self.reg_vectorizer = TfidfVectorizer()
        self.code_vectorizer = TfidfVectorizer()
        
        self.reg_matrix = None
        self.code_matrix = None
        
        self._refresh_indices()

    def _refresh_indices(self):
        """디렉토리를 스캔하여 인덱스를 새로 고침합니다."""
        # 1. 규제 문서 스캔
        self.reg_docs = []
        if self.reg_path.exists():
            for f in self.reg_path.glob("*.md"):
                try:
                    content = f.read_text(encoding='utf-8')
                    # 청크로 쪼갤 수도 있으나, 현재는 파일 단위로 처리
                    self.reg_docs.append({"name": f.name, "content": content})
                except Exception as e:
                    logger.warning(f"Failed to read {f.name}: {e}")
        
        if self.reg_docs:
            texts = [d["content"] for d in self.reg_docs]
            self.reg_matrix = self.reg_vectorizer.fit_transform(texts)
            logger.info(f"Indexed {len(self.reg_docs)} regulation documents.")
            
        # 1.1 필수(Mandatory) 규정 별도 관리 (파일명에 'safety'나 'egress'가 포함되거나 수동 지정)
        self.mandatory_content = self._generate_mandatory_summary()

    def _generate_mandatory_summary(self) -> str:
        """모든 작업에서 항상 참조해야 하는 핵심 규정 요약본을 생성합니다."""
        mandatory = []
        # 예: 안전, 피난, 최소 치수 등은 항상 포함
        keywords = ["safety", "egress", "dimensions"]
        for doc in self.reg_docs:
            if any(k in doc["name"].lower() for k in keywords):
                # 전체를 다 넣으면 너무 크므로, 제목과 핵심 요약만 추출하거나 일단 상단부만 포함
                lines = doc["content"].split('\n')[:15] # 상단 15줄만 샘플링
                mandatory.append(f"### [MANDATORY] {doc['name']}\n" + '\n'.join(lines) + "\n...")
        
        return "\n\n".join(mandatory) if mandatory else "No mandatory global regulations defined."

        # 2. 코드 샘플 스캔
        self.code_docs = []
        if self.code_path.exists():
            for f in self.code_path.glob("*.txt"):
                try:
                    content = f.read_text(encoding='utf-8')
                    self.code_docs.append({"name": f.name, "content": content})
                except Exception as e:
                    logger.warning(f"Failed to read {f.name}: {e}")
        
        if self.code_docs:
            texts = [d["content"] for d in self.code_docs]
            self.code_matrix = self.code_vectorizer.fit_transform(texts)
            logger.info(f"Indexed {len(self.code_docs)} code sample documents.")

    def retrieve_regulations(self, query: str, top_k: int = 2) -> str:
        """사용자 쿼리에 가장 적합한 건축 법규 정보를 반환합니다. (필수 규정 포함)"""
        results = []
        if self.mandatory_content:
            results.append("## ⚠️ 필수 준수 규정 (Global Constraints)\n" + self.mandatory_content)
            
        if self.reg_docs and self.reg_matrix is not None:
            search_res = self._search(query, self.reg_vectorizer, self.reg_matrix, self.reg_docs, top_k, "Related Regulation")
            if search_res:
                results.append("## 🔍 요청 관련 참조 규정 (Contextual)\n" + search_res)
            
        return "\n\n".join(results)

    def retrieve_code_samples(self, query: str, top_k: int = 1) -> str:
        """사용자 쿼리에 가장 적합한 ifcopenshell 코드 샘플을 반환합니다."""
        if not self.code_docs or self.code_matrix is None:
            return ""
            
        return self._search(query, self.code_vectorizer, self.code_matrix, self.code_docs, top_k, "Knowledge")

    def _search(self, query: str, vectorizer: TfidfVectorizer, matrix, docs, top_k: int, prefix: str) -> str:
        try:
            query_vec = vectorizer.transform([query])
            similarities = cosine_similarity(query_vec, matrix).flatten()
            
            # 유사도 순 정렬
            related_indices = similarities.argsort()[::-1][:top_k]
            
            # 유사도가 0인 것은 제외 (검색 결과 없음 처리)
            results = []
            for idx in related_indices:
                if similarities[idx] > 0:
                    doc = docs[idx]
                    results.append(f"### {prefix}: {doc['name']} (score: {similarities[idx]:.2f})\n{doc['content']}")
            
            return "\n\n".join(results)
        except Exception as e:
            logger.error(f"Search error: {e}")
            # 폴백: 아무것도 안 나옴 (이미 다 읽는 방식이 문제였으므로 안전함)
            return ""

# 싱글톤 인스턴스 (성능 및 일관성)
rag_manager = RAGManager()
