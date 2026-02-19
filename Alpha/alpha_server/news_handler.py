import feedparser
import pandas as pd
from datetime import datetime, timedelta
from textblob import TextBlob
import re

def fetch_news(ticker, days=7):
    """Yahoo Finance RSS에서 뉴스를 가져옵니다."""
    url = f"https://finance.yahoo.com/rss/headline?s={ticker}"
    
    try:
        feed = feedparser.parse(url)
        news_list = []
        
        cutoff_date = datetime.now() - timedelta(days=days)
        
        for entry in feed.entries:
            try:
                pub_date = datetime(*entry.published_parsed[:6])
                if pub_date < cutoff_date:
                    continue
                    
                news_list.append({
                    'date': pub_date,
                    'title': entry.title,
                    'summary': entry.get('summary', ''),
                    'link': entry.link
                })
            except:
                continue
        
        return pd.DataFrame(news_list)
    except Exception as e:
        print(f"뉴스 가져오기 실패 ({ticker}): {e}")
        return pd.DataFrame()

def analyze_sentiment(text):
    """텍스트의 감성을 분석합니다 (-1 ~ +1)."""
    if not text:
        return 0.0
    
    # 간단한 전처리
    text = re.sub(r'http\S+', '', text)
    
    try:
        blob = TextBlob(text)
        return blob.sentiment.polarity
    except:
        return 0.0

def get_news_features(ticker, days=7):
    """뉴스 기반 특성을 생성합니다."""
    news_df = fetch_news(ticker, days=days)
    
    if news_df.empty:
        return {
            'news_sentiment': 0.0,
            'news_volume': 0,
            'sentiment_std': 0.0
        }
    
    # 감성 분석
    news_df['sentiment'] = news_df.apply(
        lambda row: analyze_sentiment(row['title'] + ' ' + row['summary']), 
        axis=1
    )
    
    return {
        'news_sentiment': news_df['sentiment'].mean(),
        'news_volume': len(news_df),
        'sentiment_std': news_df['sentiment'].std() if len(news_df) > 1 else 0.0
    }

def get_daily_sentiment(ticker, start_date, end_date):
    """일별 감성 점수를 반환합니다 (모델 학습용)."""
    news_df = fetch_news(ticker, days=365)  # 1년치
    
    if news_df.empty:
        return pd.DataFrame()
    
    news_df['sentiment'] = news_df.apply(
        lambda row: analyze_sentiment(row['title'] + ' ' + row['summary']), 
        axis=1
    )
    
    # 일별 집계
    news_df['date_only'] = news_df['date'].dt.date
    daily = news_df.groupby('date_only').agg({
        'sentiment': 'mean',
        'title': 'count'
    }).rename(columns={'title': 'news_count'})
    
    # 날짜 범위로 reindex (뉴스 없는 날은 0)
    date_range = pd.date_range(start=start_date, end=end_date, freq='D')
    daily = daily.reindex(date_range.date, fill_value=0)
    
    return daily
