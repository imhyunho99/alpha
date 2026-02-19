#!/usr/bin/env python3
"""
Alpha 투자 전략 백테스팅
1년간 단기/중기/장기 추천을 1/3씩 매입하는 전략의 성과 분석
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'alpha_server'))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from alpha_server.asset_screener import get_all_tickers
from alpha_server.data_handler import load_data
from alpha_server.scoring_engine import calculate_scores
import json

def get_historical_recommendations(date, horizon, top_n=10):
    """특정 날짜의 추천 목록 생성"""
    tickers = get_all_tickers()
    scores = []
    
    for ticker in tickers:
        try:
            data = load_data(ticker)
            if data is None or len(data) < 100:
                continue
            
            # 해당 날짜까지의 데이터만 사용
            data = data[data.index <= date]
            if len(data) < 100:
                continue
                
            score_dict = calculate_scores(ticker, data)
            if score_dict and horizon in score_dict:
                scores.append({
                    'ticker': ticker,
                    'score': score_dict[horizon],
                    'price': data['Close'].iloc[-1]
                })
        except Exception as e:
            continue
    
    scores.sort(key=lambda x: x['score'], reverse=True)
    return scores[:top_n]

def calculate_return(ticker, buy_date, sell_date):
    """특정 기간의 수익률 계산"""
    try:
        data = load_data(ticker)
        if data is None:
            return None
        
        buy_data = data[data.index <= buy_date]
        sell_data = data[data.index <= sell_date]
        
        if len(buy_data) == 0 or len(sell_data) == 0:
            return None
            
        buy_price = buy_data['Close'].iloc[-1]
        sell_price = sell_data['Close'].iloc[-1]
        
        return (sell_price - buy_price) / buy_price * 100
    except:
        return None

def run_backtest():
    """1년 백테스팅 실행"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)
    
    # 월별로 리밸런싱 (12회)
    results = {
        'short': [],
        'medium': [],
        'long': []
    }
    
    monthly_snapshots = []
    
    print("백테스팅 시작...")
    print(f"기간: {start_date.date()} ~ {end_date.date()}")
    print("=" * 80)
    
    for month in range(12):
        rebalance_date = start_date + timedelta(days=30 * month)
        next_rebalance = start_date + timedelta(days=30 * (month + 1))
        
        print(f"\n[{month + 1}월차] 리밸런싱 날짜: {rebalance_date.date()}")
        
        snapshot = {
            'date': rebalance_date.strftime('%Y-%m-%d'),
            'holdings': {}
        }
        
        for horizon in ['short', 'medium', 'long']:
            print(f"  {horizon} 추천 생성 중...")
            recommendations = get_historical_recommendations(rebalance_date, horizon, top_n=10)
            
            if not recommendations:
                print(f"    경고: {horizon} 추천 없음")
                continue
            
            snapshot['holdings'][horizon] = [r['ticker'] for r in recommendations]
            
            # 각 추천 자산의 수익률 계산
            for rec in recommendations:
                ret = calculate_return(rec['ticker'], rebalance_date, next_rebalance)
                if ret is not None:
                    results[horizon].append({
                        'month': month + 1,
                        'ticker': rec['ticker'],
                        'score': rec['score'],
                        'return': ret
                    })
            
            print(f"    완료: {len(recommendations)}개 자산")
        
        monthly_snapshots.append(snapshot)
    
    return results, monthly_snapshots

def generate_report(results, snapshots):
    """백테스팅 결과 보고서 생성"""
    report = []
    report.append("=" * 80)
    report.append("Alpha 투자 전략 백테스팅 보고서")
    report.append("=" * 80)
    report.append(f"생성 일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"백테스팅 기간: 1년 (12개월)")
    report.append(f"리밸런싱 주기: 월간")
    report.append("")
    
    # 전략 설명
    report.append("=" * 80)
    report.append("투자 전략")
    report.append("=" * 80)
    report.append("• 매월 초 단기/중기/장기 Top 10 추천을 각각 받음")
    report.append("• 총 30개 자산에 균등 분산 투자 (각 자산 3.33%)")
    report.append("• 단기/중기/장기 비중: 1/3씩 (각 33.33%)")
    report.append("• 월간 리밸런싱으로 포트폴리오 재구성")
    report.append("")
    
    # 전체 성과
    report.append("=" * 80)
    report.append("전체 성과 요약")
    report.append("=" * 80)
    
    total_trades = 0
    total_return = 0
    horizon_stats = {}
    
    for horizon in ['short', 'medium', 'long']:
        trades = results[horizon]
        if not trades:
            continue
            
        returns = [t['return'] for t in trades]
        total_trades += len(returns)
        
        avg_return = np.mean(returns)
        total_return += avg_return / 3  # 1/3 비중
        
        horizon_stats[horizon] = {
            'trades': len(returns),
            'avg_return': avg_return,
            'median_return': np.median(returns),
            'std': np.std(returns),
            'min': np.min(returns),
            'max': np.max(returns),
            'win_rate': len([r for r in returns if r > 0]) / len(returns) * 100
        }
    
    report.append(f"총 거래 횟수: {total_trades}회")
    report.append(f"연간 예상 수익률: {total_return:.2f}%")
    report.append(f"월평균 수익률: {total_return / 12:.2f}%")
    report.append("")
    
    # 기간별 상세 성과
    report.append("=" * 80)
    report.append("기간별 상세 성과")
    report.append("=" * 80)
    
    horizon_names = {
        'short': '단기 (1주)',
        'medium': '중기 (3개월)',
        'long': '장기 (1년)'
    }
    
    for horizon in ['short', 'medium', 'long']:
        if horizon not in horizon_stats:
            continue
            
        stats = horizon_stats[horizon]
        report.append(f"\n[{horizon_names[horizon]}]")
        report.append(f"  거래 횟수: {stats['trades']}회")
        report.append(f"  평균 수익률: {stats['avg_return']:.2f}%")
        report.append(f"  중앙값 수익률: {stats['median_return']:.2f}%")
        report.append(f"  표준편차: {stats['std']:.2f}%")
        report.append(f"  최소/최대: {stats['min']:.2f}% / {stats['max']:.2f}%")
        report.append(f"  승률: {stats['win_rate']:.1f}%")
    
    # 월별 성과
    report.append("\n" + "=" * 80)
    report.append("월별 성과 추이")
    report.append("=" * 80)
    
    for month in range(1, 13):
        month_returns = []
        for horizon in ['short', 'medium', 'long']:
            month_trades = [t['return'] for t in results[horizon] if t['month'] == month]
            if month_trades:
                month_returns.extend(month_trades)
        
        if month_returns:
            avg = np.mean(month_returns)
            report.append(f"{month:2d}월차: {avg:6.2f}% (거래 {len(month_returns)}회)")
    
    # 베스트/워스트 자산
    report.append("\n" + "=" * 80)
    report.append("베스트/워스트 자산")
    report.append("=" * 80)
    
    all_trades = []
    for horizon in ['short', 'medium', 'long']:
        all_trades.extend(results[horizon])
    
    all_trades.sort(key=lambda x: x['return'], reverse=True)
    
    report.append("\n[Top 10 수익률]")
    for i, trade in enumerate(all_trades[:10], 1):
        report.append(f"{i:2d}. {trade['ticker']:15s} {trade['return']:7.2f}% ({trade['month']}월차)")
    
    report.append("\n[Worst 10 수익률]")
    for i, trade in enumerate(all_trades[-10:], 1):
        report.append(f"{i:2d}. {trade['ticker']:15s} {trade['return']:7.2f}% ({trade['month']}월차)")
    
    # 투자 시뮬레이션
    report.append("\n" + "=" * 80)
    report.append("투자 시뮬레이션 (초기 자본 $10,000)")
    report.append("=" * 80)
    
    initial_capital = 10000
    final_capital = initial_capital * (1 + total_return / 100)
    profit = final_capital - initial_capital
    
    report.append(f"초기 자본: ${initial_capital:,.2f}")
    report.append(f"최종 자본: ${final_capital:,.2f}")
    report.append(f"순이익: ${profit:,.2f}")
    report.append(f"수익률: {total_return:.2f}%")
    
    # 벤치마크 비교 (SPY)
    report.append("\n" + "=" * 80)
    report.append("벤치마크 비교")
    report.append("=" * 80)
    
    try:
        spy_return = calculate_return('SPY', 
                                     datetime.now() - timedelta(days=365), 
                                     datetime.now())
        if spy_return:
            report.append(f"S&P 500 (SPY) 수익률: {spy_return:.2f}%")
            report.append(f"Alpha 전략 초과 수익: {total_return - spy_return:.2f}%")
    except:
        report.append("벤치마크 데이터 없음")
    
    report.append("\n" + "=" * 80)
    report.append("보고서 끝")
    report.append("=" * 80)
    
    return "\n".join(report)

if __name__ == "__main__":
    print("Alpha 투자 전략 백테스팅을 시작합니다...")
    print("이 작업은 수 분이 소요될 수 있습니다.\n")
    
    results, snapshots = run_backtest()
    
    report = generate_report(results, snapshots)
    
    # 보고서 저장
    report_path = "/Users/nahyeonho/pythonWorkspace/Alpha/BACKTEST_REPORT.md"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    # 상세 데이터 저장
    data_path = "/Users/nahyeonho/pythonWorkspace/Alpha/backtest_data.json"
    with open(data_path, 'w', encoding='utf-8') as f:
        json.dump({
            'results': results,
            'snapshots': snapshots
        }, f, indent=2)
    
    print("\n" + report)
    print(f"\n보고서 저장: {report_path}")
    print(f"상세 데이터 저장: {data_path}")
