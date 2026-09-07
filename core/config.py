import os
from dataclasses import dataclass, field
from typing import Dict
from dotenv import load_dotenv

# .env 파일이 존재하면 자동 로드
load_dotenv()

@dataclass(frozen=True)
class DatabaseSettings:
    dbname: str = os.getenv("DB_NAME", "stock_db")
    user: str = os.getenv("DB_USER", "postgres")
    password: str = os.getenv("DB_PASSWORD", "")
    host: str = os.getenv("DB_HOST", "localhost")
    port: str = os.getenv("DB_PORT", "5432")

    @property
    def connection_kwargs(self) -> dict:
        return {
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
            "host": self.host,
            "port": self.port,
        }

@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    default_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

@dataclass(frozen=True)
class GeminiSettings:
    api_key: str = os.getenv("GEMINI_API_KEY", "")
    model_name: str = os.getenv("GEMINI_MODEL_NAME", "gemini-3.6-flash")

@dataclass(frozen=True)
class Settings:
    db: DatabaseSettings = field(default_factory=DatabaseSettings)
    telegram: TelegramSettings = field(default_factory=TelegramSettings)
    gemini: GeminiSettings = field(default_factory=GeminiSettings)

    # 11대 섹터 ETF 매핑
    sector_to_etf: Dict[str, str] = field(default_factory=lambda: {
        "Technology": "XLK",
        "Financial Services": "XLF",
        "Financials": "XLF",
        "Consumer Cyclical": "XLY",
        "Communication Services": "XLC",
        "Healthcare": "XLV",
        "Industrials": "XLI",
        "Consumer Defensive": "XLP",
        "Energy": "XLE",
        "Basic Materials": "XLB",
        "Utilities": "XLU",
        "Real Estate": "XLRE",
    })

    custom_sector_map: Dict[str, str] = field(default_factory=lambda: {
        "CRCL": "XLF",
        "COIN": "FINX",
        "HOOD": "FINX",
        "MSTR": "XLK",
        "NOK": "XLC",
        "NVDA": "SOXX",
        "AMD": "SOXX",
        "TSM": "SOXX",
        "AVGO": "SOXX",
        "MSFT": "IGV",
        "CRM": "IGV",
        "PLTR": "IGV",
        "PANW": "CIBR",
        "CRWD": "CIBR",
    })

    # 정밀 자금 추적용 서브섹터 ETF 목록
    subsector_etfs: Dict[str, str] = field(default_factory=lambda: {
        "SOXX": "반도체 (Semiconductors)",
        "SMH": "반도체 대형주 (VanEck Semi)",
        "IGV": "소프트웨어/SaaS (Tech-Software)",
        "CIBR": "사이버보안 (Cybersecurity)",
        "BOTZ": "AI 및 로보틱스 (Robotics & AI)",
        "XBI": "혁신 바이오테크 (Biotech)",
        "FINX": "핀테크 (Fintech)",
        "URA": "우라늄/원자력 에너지 (Uranium)",
        "XLE": "에너지 (Energy)",
        "XLF": "전통 금융 (Financials)",
    })

# 싱글톤 설정 인스턴스
settings = Settings()
