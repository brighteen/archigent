'''
preference_manager.py - ArchiGent Preference Manager
===================================================
사용자의 주관적 취향을 분석하고 가중치(Weights)로 관리합니다.
'''

import json
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)

class PreferenceManager:
    def __init__(self, profile_path: str = "style_profile.json"):
        self.profile_path = Path(profile_path)
        self.default_profile = {
            "weights": {
                "modern_aesthetic": 0.5,
                "functional_efficiency": 0.5,
                "open_space_ratio": 0.5,
                "minimalism": 0.5
            },
            "selection_history": []
        }
        self.profile = self.load_profile()

    def load_profile(self) -> Dict[str, Any]:
        if self.profile_path.exists():
            try:
                with open(self.profile_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load preference profile: {e}")
        return self.default_profile.copy()

    def save_profile(self):
        try:
            with open(self.profile_path, "w", encoding="utf-8") as f:
                json.dump(self.profile, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save preference profile: {e}")

    def update_preference(self, selected_option: Dict[str, Any]):
        """사용자의 선택을 바탕으로 가중치를 업데이트 (Heuristic)"""
        weights = self.profile["weights"]
        features = selected_option.get("features", {})
        
        # 간단한 학습 로직: 선택된 옵션의 특징 방향으로 가중치 미세 조정 (alpha=0.1)
        alpha = 0.1
        for k, v in features.items():
            if k in weights:
                weights[k] = round(weights[k] * (1 - alpha) + v * alpha, 3)
        
        self.profile["selection_history"].append({
            "timestamp": logger.name, # Simple placeholder
            "option_id": selected_option.get("id"),
            "features": features
        })
        self.save_profile()
        return weights

    def get_profile_summary(self) -> str:
        """Planner 프롬프트에 삽입할 요약 문자열"""
        w = self.profile["weights"]
        return f"현재 사용자 스타일 선호도 (0~1): 현대미({w['modern_aesthetic']}), 기능성({w['functional_efficiency']}), 개방감({w['open_space_ratio']}), 미니멀리즘({w['minimalism']})"
