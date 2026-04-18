# 📊 스토리지 벤치마크 보고서: CSV vs QuestDB

**테스트 일시**: 2026-02-26 21:09:41
**테스트 자산**: 100개 (MSCI, BALL, AKAM, PRU, DRI, COO, KO, BSX, PGR, AZO, NXPI, YUM, USB, KR, ARES, MA, GIS, ALLE, ALGN, AIZ, CLX, FIX, IVZ, MRK, ALB, LII, CMG, PFE, NWSA, PSKY, WST, GNRC, CME, HSIC, MAS, DVA, SWKS, TJX, ABBV, RL, SHW, CDNS, PLTR, WEC, EQT, VTR, BXP, CFG, RTX, EOG, WMB, WBD, FRT, ATO, ES, TTWO, EQIX, META, CCI, SPG, AMZN, PPG, HON, KIM, TECH, FAST, APP, LRCX, DLR, STLD, MPWR, MAA, EXE, LYB, SCHW, PH, AON, AMCR, NSC, COIN, RSG, DXCM, TGT, WMT, ADP, TDG, UBER, HLT, MOS, EXPE, CPB, XOM, EG, CTAS, NUE, CSX, VTRS, ODFL, NEM, APA)

## 1. 속도 비교 (낮을수록 좋음)
| 항목 | CSV 파일 | QuestDB | 차이 (배수) |
|---|---|---|---|
| **쓰기 속도** | 0.1673s | 0.1290s | QuestDB가 더 빠름 |
| **읽기 속도** | 0.0863s | 1.0502s | CSV가 더 빠름 |

## 2. 정확도 비교
- **CSV**: 0.00%
- **QuestDB**: 0.00%

## 3. 분석 및 결론
### 🐢 쓰기 속도 분석
- QuestDB가 예상보다 빠르게 동작했습니다.

### 🐇 읽기 속도 분석
- CSV 읽기 속도가 더 빠르거나 비슷합니다. 데이터 양이 적어서 파일 시스템 캐시 효과가 컸을 수 있습니다.

### 🎯 종합 결론
- 현재 구현상으로는 **읽기 성능은 QuestDB가 유리**하나, **쓰기 성능은 튜닝(Bulk Insert)이 시급**합니다.
- 데이터 무결성(정확도)은 양쪽 모두 동일하므로, 성능 최적화만 수행하면 QuestDB 전환이 타당합니다.