"""
Subsector Classification & Database Migration Script
Updates tickers table with detailed, granular subsectors for:
- SEMICONDUCTOR (반도체)
- OPTICAL (광통신/네트워킹)
- CRYPTO (암호화폐/채굴)
- QUANTUM (양자컴퓨터)
- CYBERSECURITY (사이버보안)
- AI_HARDWARE (AI서버/스토리지)
- AI_SOFTWARE (클라우드/SaaS/AI)
- NUCLEAR (원자력/SMR/우라늄)
- BIOTECH (혁신 바이오)
- FINTECH (핀테크)
- EV (전기차/자율주행)
- DEFENSE (방산/우주항공)
- LITHIUM (배터리/리튬)
- DATA_CENTER (데이터센터/통신탑)
"""
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.database import get_db_cursor
from core.logger import get_logger

logger = get_logger("subsector_migration")

SUBSECTOR_MAP = {
    # 1. 기술주 세분화: 반도체 (SEMICONDUCTOR)
    "NVDA": ("SEMICONDUCTOR", "SOXX"),
    "AMD": ("SEMICONDUCTOR", "SOXX"),
    "TSM": ("SEMICONDUCTOR", "SOXX"),
    "AVGO": ("SEMICONDUCTOR", "SOXX"),
    "ASML": ("SEMICONDUCTOR", "SOXX"),
    "ARM": ("SEMICONDUCTOR", "SOXX"),
    "QCOM": ("SEMICONDUCTOR", "SOXX"),
    "MU": ("SEMICONDUCTOR", "SOXX"),
    "INTC": ("SEMICONDUCTOR", "SOXX"),
    "LRCX": ("SEMICONDUCTOR", "SOXX"),
    "AMAT": ("SEMICONDUCTOR", "SOXX"),
    "KLAC": ("SEMICONDUCTOR", "SOXX"),
    "TXN": ("SEMICONDUCTOR", "SOXX"),
    "ADI": ("SEMICONDUCTOR", "SOXX"),
    "MRVL": ("SEMICONDUCTOR", "SOXX"),
    "ON": ("SEMICONDUCTOR", "SOXX"),
    "NXPI": ("SEMICONDUCTOR", "SOXX"),
    "MPWR": ("SEMICONDUCTOR", "SOXX"),
    "CAMT": ("SEMICONDUCTOR", "SOXX"),
    "ACLS": ("SEMICONDUCTOR", "SOXX"),
    "FORM": ("SEMICONDUCTOR", "SOXX"),
    "AEHR": ("SEMICONDUCTOR", "SOXX"),
    "ALAB": ("SEMICONDUCTOR", "SOXX"),
    "SITM": ("SEMICONDUCTOR", "SOXX"),

    # 2. 기술주 세분화: 광통신 / 네트워크 하드웨어 (OPTICAL)
    "AAOI": ("OPTICAL", "IYZ"),
    "LITE": ("OPTICAL", "IYZ"),
    "COHR": ("OPTICAL", "IYZ"),
    "CIEN": ("OPTICAL", "IYZ"),
    "POET": ("OPTICAL", "IYZ"),
    "CRDO": ("OPTICAL", "IYZ"),
    "AKAM": ("OPTICAL", "IYZ"),
    "NOK": ("OPTICAL", "IYZ"),

    # 3. 기술주 세분화: 암호화폐 / 비트코인 채굴 (CRYPTO)
    "MSTR": ("CRYPTO", "WGMI"),
    "MARA": ("CRYPTO", "WGMI"),
    "RIOT": ("CRYPTO", "WGMI"),
    "CLSK": ("CRYPTO", "WGMI"),
    "CIFR": ("CRYPTO", "WGMI"),
    "BITF": ("CRYPTO", "WGMI"),
    "HUT": ("CRYPTO", "WGMI"),
    "IREN": ("CRYPTO", "WGMI"),
    "WULF": ("CRYPTO", "WGMI"),
    "CAN": ("CRYPTO", "WGMI"),
    "CORZ": ("CRYPTO", "WGMI"),
    "COIN": ("CRYPTO", "WGMI"),

    # 4. 기술주 세분화: 양자컴퓨터 (QUANTUM)
    "IONQ": ("QUANTUM", "QTUM"),
    "RGTI": ("QUANTUM", "QTUM"),
    "QBTS": ("QUANTUM", "QTUM"),
    "QUBT": ("QUANTUM", "QTUM"),

    # 5. 기술주 세분화: 사이버보안 (CYBERSECURITY)
    "CRWD": ("CYBERSECURITY", "CIBR"),
    "PANW": ("CYBERSECURITY", "CIBR"),
    "FTNT": ("CYBERSECURITY", "CIBR"),
    "CYBR": ("CYBERSECURITY", "CIBR"),
    "NET": ("CYBERSECURITY", "CIBR"),
    "TENB": ("CYBERSECURITY", "CIBR"),
    "QLYS": ("CYBERSECURITY", "CIBR"),
    "RPD": ("CYBERSECURITY", "CIBR"),
    "ZS": ("CYBERSECURITY", "CIBR"),

    # 6. 기술주 세분화: AI 하드웨어 / 서버 / 스토리지 (AI_HARDWARE)
    "SMCI": ("AI_HARDWARE", "BOTZ"),
    "DELL": ("AI_HARDWARE", "BOTZ"),
    "PSTG": ("AI_HARDWARE", "BOTZ"),
    "STX": ("AI_HARDWARE", "BOTZ"),
    "WDC": ("AI_HARDWARE", "BOTZ"),
    "SNDK": ("AI_HARDWARE", "BOTZ"),
    "ENVX": ("AI_HARDWARE", "BOTZ"),
    "OUST": ("AI_HARDWARE", "BOTZ"),

    # 7. 기술주 세분화: AI 소프트웨어 & 클라우드 SaaS (AI_SOFTWARE)
    "PLTR": ("AI_SOFTWARE", "IGV"),
    "MSFT": ("AI_SOFTWARE", "IGV"),
    "CRM": ("AI_SOFTWARE", "IGV"),
    "ORCL": ("AI_SOFTWARE", "IGV"),
    "SNOW": ("AI_SOFTWARE", "IGV"),
    "DDOG": ("AI_SOFTWARE", "IGV"),
    "MDB": ("AI_SOFTWARE", "IGV"),
    "NOW": ("AI_SOFTWARE", "IGV"),
    "ADBE": ("AI_SOFTWARE", "IGV"),
    "INTU": ("AI_SOFTWARE", "IGV"),
    "GTLB": ("AI_SOFTWARE", "IGV"),
    "CFLT": ("AI_SOFTWARE", "IGV"),
    "PATH": ("AI_SOFTWARE", "IGV"),
    "AI": ("AI_SOFTWARE", "IGV"),
    "BBAI": ("AI_SOFTWARE", "IGV"),
    "SOUN": ("AI_SOFTWARE", "IGV"),
    "BOX": ("AI_SOFTWARE", "IGV"),
    "DBX": ("AI_SOFTWARE", "IGV"),
    "DOCU": ("AI_SOFTWARE", "IGV"),
    "ESTC": ("AI_SOFTWARE", "IGV"),
    "FSLY": ("AI_SOFTWARE", "IGV"),
    "HUBS": ("AI_SOFTWARE", "IGV"),
    "TEAM": ("AI_SOFTWARE", "IGV"),
    "TOST": ("AI_SOFTWARE", "IGV"),
    "TWLO": ("AI_SOFTWARE", "IGV"),
    "VERI": ("AI_SOFTWARE", "IGV"),
    "WDAY": ("AI_SOFTWARE", "IGV"),
    "APP": ("AI_SOFTWARE", "IGV"),
    "AUR": ("AI_SOFTWARE", "IGV"),
    "SPIR": ("AI_SOFTWARE", "IGV"),
    "UPBD": ("AI_SOFTWARE", "IGV"),

    # 8. 클린에너지 / 태양광 (CLEAN_ENERGY)
    "ENPH": ("CLEAN_ENERGY", "TAN"),
    "FSLR": ("CLEAN_ENERGY", "TAN"),
    "SEDG": ("CLEAN_ENERGY", "TAN"),

    # 9. 원자력 / SMR 전력 / 우라늄 (NUCLEAR)
    "OKLO": ("NUCLEAR", "URA"),
    "SMR": ("NUCLEAR", "URA"),
    "NNE": ("NUCLEAR", "URA"),
    "CEG": ("NUCLEAR", "URA"),
    "VST": ("NUCLEAR", "URA"),
    "TLN": ("NUCLEAR", "URA"),
    "CCJ": ("NUCLEAR", "URA"),

    # 10. 우주항공 / 방산 / 드론 (DEFENSE)
    "LMT": ("DEFENSE", "XLI"),
    "NOC": ("DEFENSE", "XLI"),
    "RTX": ("DEFENSE", "XLI"),
    "GD": ("DEFENSE", "XLI"),
    "BA": ("DEFENSE", "XLI"),
    "KTOS": ("DEFENSE", "XLI"),
    "AVAV": ("DEFENSE", "XLI"),
    "RCAT": ("DEFENSE", "XLI"),
    "AXON": ("DEFENSE", "XLI"),
    "RKLB": ("DEFENSE", "XLI"),
    "LUNR": ("DEFENSE", "XLI"),
    "BKSY": ("DEFENSE", "XLI"),
    "PL": ("DEFENSE", "XLI"),
    "RDW": ("DEFENSE", "XLI"),
    "MNTS": ("DEFENSE", "XLI"),
    "LLAP": ("DEFENSE", "XLI"),
    "UAVS": ("DEFENSE", "XLI"),
    "ASTS": ("DEFENSE", "XLI"),

    # 11. 혁신 바이오테크 (BIOTECH)
    "ARGX": ("BIOTECH", "XBI"),
    "ALNY": ("BIOTECH", "XBI"),
    "BIIB": ("BIOTECH", "XBI"),
    "CRSP": ("BIOTECH", "XBI"),
    "BEAM": ("BIOTECH", "XBI"),
    "NTLA": ("BIOTECH", "XBI"),
    "BMRN": ("BIOTECH", "XBI"),
    "EXAS": ("BIOTECH", "XBI"),
    "DNA": ("BIOTECH", "XBI"),
    "BBIO": ("BIOTECH", "XBI"),
    "KURA": ("BIOTECH", "XBI"),
    "MDGL": ("BIOTECH", "XBI"),
    "RARE": ("BIOTECH", "XBI"),
    "SRPT": ("BIOTECH", "XBI"),
    "VKTX": ("BIOTECH", "XBI"),
    "ALT": ("BIOTECH", "XBI"),

    # 12. 핀테크 (FINTECH)
    "HOOD": ("FINTECH", "FINX"),
    "SOFI": ("FINTECH", "FINX"),
    "SQ": ("FINTECH", "FINX"),
    "PYPL": ("FINTECH", "FINX"),
    "AFRM": ("FINTECH", "FINX"),
    "UPST": ("FINTECH", "FINX"),
    "NU": ("FINTECH", "FINX"),

    # 13. 전기차 / 자율주행 / 모빌리티 (EV)
    "TSLA": ("EV", "XLY"),
    "RIVN": ("EV", "XLY"),
    "LCID": ("EV", "XLY"),
    "NIO": ("EV", "XLY"),
    "XPEV": ("EV", "XLY"),
    "LI": ("EV", "XLY"),
    "LAZR": ("EV", "XLY"),
    "HSAI": ("EV", "XLY"),
    "QS": ("EV", "XLY"),
    "SLDP": ("EV", "XLY"),

    # 14. 리튬 / 핵심 소재 (LITHIUM)
    "ALB": ("LITHIUM", "XLB"),
    "SQM": ("LITHIUM", "XLB"),
    "LAC": ("LITHIUM", "XLB"),
    "LTHM": ("LITHIUM", "XLB"),
    "PLL": ("LITHIUM", "XLB"),
    "MP": ("LITHIUM", "XLB"),

    # 15. 데이터센터 / 통신탑 리츠 (DATA_CENTER)
    "DLR": ("DATA_CENTER", "XLRE"),
    "EQIX": ("DATA_CENTER", "XLRE"),
    "AMT": ("DATA_CENTER", "XLRE"),
    "CCI": ("DATA_CENTER", "XLRE"),
}

def migrate_subsectors():
    logger.info("🛠 Migrating tickers table with granular subsectors...")
    updated_count = 0
    with get_db_cursor(commit=True) as (cur, _):
        for ticker, (subsector, sector_etf) in SUBSECTOR_MAP.items():
            cur.execute("""
                UPDATE tickers 
                SET subsector = %s, sector_etf = %s, is_ai_classified = TRUE
                WHERE ticker = %s;
            """, (subsector, sector_etf, ticker))
            if cur.rowcount > 0:
                updated_count += cur.rowcount

        # 나머지는 기본 sector_etf에 따라 기본 subsector 부여
        cur.execute("""
            UPDATE tickers 
            SET subsector = sector_etf 
            WHERE subsector IS NULL;
        """)

    logger.info(f"✅ Successfully updated {updated_count} specialized tickers with granular subsectors.")

if __name__ == "__main__":
    migrate_subsectors()
