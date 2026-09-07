import logging
import sys

def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """프로젝트 표준 포맷 로거 생성 유틸리티 (Windows UTF-8 인코딩 지원)"""
    # Windows 콘솔 한글 및 이모지 출력 인코딩 보정
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(level)
    return logger
