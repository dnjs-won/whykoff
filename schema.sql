-- ====================================================================
-- Whykoff Stock System - PostgreSQL Database Schema (schema.sql)
-- 기존 stock_db의 18만 건 OHLCV 데이터와 100% 호환되며, 
-- 스냅샷/포지션 분리 및 백테스트 게이트를 지원하는 확장 스키마
-- ====================================================================

-- 1. 추적 종목 관리 테이블 (tickers)
CREATE TABLE IF NOT EXISTS tickers (
    ticker VARCHAR(20) PRIMARY KEY,
    query_ticker VARCHAR(20),
    asset_class VARCHAR(20) DEFAULT 'US_STOCK',
    sector_etf VARCHAR(10),            -- 대분류 섹터 ETF (XLF, XLY 등)
    subsector VARCHAR(30),             -- 정밀 서브섹터 (SOXX, IGV, CRYPTO, CIBR 등)
    is_ai_classified BOOLEAN DEFAULT FALSE, -- AI 1회성 자동분류 완료 여부
    is_active BOOLEAN DEFAULT TRUE,
    is_reported BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    score_updated_at TIMESTAMP WITH TIME ZONE
);

-- 기존 DB 호환을 위한 신규 컬럼 안전 추가 (마이그레이션)
ALTER TABLE tickers ADD COLUMN IF NOT EXISTS subsector VARCHAR(30);
ALTER TABLE tickers ADD COLUMN IF NOT EXISTS is_ai_classified BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_tickers_sector ON tickers (sector_etf);
CREATE INDEX IF NOT EXISTS idx_tickers_subsector ON tickers (subsector);
CREATE INDEX IF NOT EXISTS idx_tickers_active ON tickers (is_active);

-- 2. 일봉 데이터 (ohlcv_daily) - 16만+ 건 누적
CREATE TABLE IF NOT EXISTS ohlcv_daily (
    ticker VARCHAR(20) NOT NULL,
    datetime TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    open NUMERIC(14, 4) NOT NULL,
    high NUMERIC(14, 4) NOT NULL,
    low NUMERIC(14, 4) NOT NULL,
    close NUMERIC(14, 4) NOT NULL,
    volume BIGINT NOT NULL,
    PRIMARY KEY (ticker, datetime)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_daily_ticker_dt ON ohlcv_daily (ticker, datetime DESC);

-- 3. 1시간봉 데이터 (ohlcv_1h) - 18만+ 건 누적
CREATE TABLE IF NOT EXISTS ohlcv_1h (
    ticker VARCHAR(20) NOT NULL,
    datetime TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    open NUMERIC(14, 4) NOT NULL,
    high NUMERIC(14, 4) NOT NULL,
    low NUMERIC(14, 4) NOT NULL,
    close NUMERIC(14, 4) NOT NULL,
    volume BIGINT NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (ticker, datetime)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_1h_ticker_dt ON ohlcv_1h (ticker, datetime DESC);

-- 4. 거시 환경 지표 (macro_indicators)
CREATE TABLE IF NOT EXISTS macro_indicators (
    id BIGSERIAL PRIMARY KEY,
    metric_code VARCHAR(30),
    ticker VARCHAR(20) NOT NULL,
    value NUMERIC(14, 4) NOT NULL,
    change_pct NUMERIC(8, 4),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT uq_macro_metric_date UNIQUE (metric_code, ticker, updated_at)
);

CREATE INDEX IF NOT EXISTS idx_macro_metric ON macro_indicators (metric_code, updated_at DESC);

-- 5. 섹터 / 서브섹터 유동성 점유율 (sector_liquidity_shares)
CREATE TABLE IF NOT EXISTS sector_liquidity_shares (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL,        -- XLK, SOXX, IGV, XLF, WGMI 등
    sector_name VARCHAR(50),
    dollar_volume NUMERIC(20, 2),
    market_share_pct NUMERIC(8, 4),
    share_20ma NUMERIC(8, 4),
    share_delta NUMERIC(8, 4),          -- 자금 점유율 변화폭
    relative_rs_spy NUMERIC(8, 4),
    change_pct NUMERIC(8, 4),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT uq_sector_date_symbol UNIQUE (trade_date, symbol)
);

CREATE INDEX IF NOT EXISTS idx_sector_shares_date ON sector_liquidity_shares (trade_date DESC, symbol);

-- 6. 종목 뉴스 (market_news)
CREATE TABLE IF NOT EXISTS market_news (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    link TEXT,
    publisher VARCHAR(100),
    published_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT uq_news_item UNIQUE (symbol, published_at, title)
);

CREATE INDEX IF NOT EXISTS idx_news_symbol_pub ON market_news (symbol, published_at DESC);

-- 7. [스마트 추적 1] 스캔 스냅샷 로그 (scan_snapshots)
-- 매 스캔 시점의 종목 점수, 지표, 별점 상태를 모두 기록 (일자별 빌드업 추적용, 중복 허용)
CREATE TABLE IF NOT EXISTS scan_snapshots (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    scan_date DATE NOT NULL,
    strategy_type VARCHAR(50) NOT NULL,  -- 'WYCKOFF_BAGGER', 'CONFLUENCE_SNIPER'
    technical_score NUMERIC(5, 2) NOT NULL, -- 지표 원점수 (0~100)
    stars_rating SMALLINT NOT NULL,      -- 스윗스팟 별점 (1~5성)
    is_sweet_spot BOOLEAN DEFAULT FALSE, -- 68~78점 스윗스팟 여부
    is_overextended BOOLEAN DEFAULT FALSE, -- 85점 이상 과열 추격 경고
    current_price NUMERIC(14, 4) NOT NULL,
    daily_poc NUMERIC(14, 4),
    stop_loss NUMERIC(14, 4),
    tp1 NUMERIC(14, 4),
    tp2 NUMERIC(14, 4),
    rr_ratio NUMERIC(5, 2),
    reasons JSONB,                       -- 가산/감점 사유 목록
    ai_rating VARCHAR(5),                -- 'A', 'B', 'C'
    ai_verdict TEXT,                     -- AI 리스크 매니저 최종 코멘트
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT uq_scan_ticker_date_strat UNIQUE (ticker, scan_date, strategy_type)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_date ON scan_snapshots (scan_date DESC, technical_score DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker ON scan_snapshots (ticker, scan_date DESC);

-- 8. [스마트 추적 2] 실전 포지션 라이프사이클 및 성과 정산 (active_trades)
-- 스캔 종목의 실제 단일 포지션 관리 (중복 진입 차단 & 1:1 정산)
CREATE TABLE IF NOT EXISTS active_trades (
    trade_id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    strategy_type VARCHAR(50) NOT NULL,
    initial_snapshot_id BIGINT REFERENCES scan_snapshots(id),
    entry_date DATE NOT NULL,
    entry_price NUMERIC(14, 4) NOT NULL,
    stop_loss NUMERIC(14, 4) NOT NULL,
    tp1 NUMERIC(14, 4) NOT NULL,
    tp2 NUMERIC(14, 4) NOT NULL,
    rr_ratio NUMERIC(5, 2),
    
    -- 상태 머신: OPEN -> TP1_HIT -> TP2_HIT / SL_HIT / EXPIRED
    status VARCHAR(20) DEFAULT 'OPEN', 
    current_price NUMERIC(14, 4),
    unrealized_pnl_pct NUMERIC(8, 4) DEFAULT 0.0,
    max_favorable_pct NUMERIC(8, 4) DEFAULT 0.0, -- MFE (최대 도달 수익률)
    max_adverse_pct NUMERIC(8, 4) DEFAULT 0.0,   -- MAE (최대 낙폭)
    
    exit_date DATE,
    exit_price NUMERIC(14, 4),
    realized_pnl_pct NUMERIC(8, 4),
    holding_days INTEGER DEFAULT 0,
    close_reason VARCHAR(50),             -- 'TP1_TARGET', 'STOP_LOSS', 'TIMEOUT_20D'
    
    reconfirmed_count INTEGER DEFAULT 1,  -- OPEN 중 추가 스캔된 횟수
    tp1_hit BOOLEAN DEFAULT FALSE,        -- 1차 목표가(TP1) 도달 및 50% 분할 익절 완료 여부
    max_holding_days INTEGER DEFAULT 20,  -- 보유 기한 (기본 20일, TP1 도달 시 40일 연장)
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 활성(OPEN) 상태인 포지션은 종목당 최대 1건만 유지되도록 고유 인덱스 보장
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_open_trade_ticker 
ON active_trades (ticker, strategy_type) 
WHERE status = 'OPEN';

CREATE INDEX IF NOT EXISTS idx_trades_status ON active_trades (status, entry_date DESC);

-- 9. 챔피언-챌린저 백테스트 성과 벤치마크 (strategy_benchmarks)
CREATE TABLE IF NOT EXISTS strategy_benchmarks (
    id BIGSERIAL PRIMARY KEY,
    strategy_version VARCHAR(50) NOT NULL, -- e.g. 'wyckoff_v1.0_champion', 'wyckoff_v1.1_challenger'
    test_start_date DATE NOT NULL,
    test_end_date DATE NOT NULL,
    sample_trades_count INTEGER NOT NULL,
    win_rate_pct NUMERIC(5, 2) NOT NULL,
    profit_factor NUMERIC(6, 2) NOT NULL,
    expectancy_pct NUMERIC(6, 2) NOT NULL,
    avg_holding_days NUMERIC(5, 2),
    max_drawdown_pct NUMERIC(5, 2),
    is_champion BOOLEAN DEFAULT FALSE,
    params_config JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
