#!/usr/bin/env python3
"""
전체 자산에 Phase 2 앙상블 모델 적용
"""
import sys
import os
sys.path.append(os.path.dirname(__file__))

from alpha_server.ensemble_handler import update_all_ensemble_models

if __name__ == "__main__":
    print("\n🚀 전체 자산에 Phase 2 앙상블 모델 적용 시작...")
    print("   (200+ 자산, 약 10-15분 소요 예상)\n")
    
    update_all_ensemble_models()
    
    print("\n✅ 완료! 서버를 재시작하여 새 모델을 사용하세요.")
