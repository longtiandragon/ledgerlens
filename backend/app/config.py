from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    data_dir: Path = Path('runtime')
    database_url: str = 'sqlite:///./runtime/ledgerlens.sqlite'
    task_mode: str = 'local'
    telemetry_enabled: bool = True
    redis_url: str = 'redis://127.0.0.1:6379/0'
    deepseek_api_key: str = ''
    deepseek_base_url: str = 'https://api.deepseek.com'
    deepseek_model: str = 'deepseek-flash'
    max_upload_bytes: int = 20 * 1024 * 1024
    process_datasets_dir: Path = Path('.local/bpi2019/process-events')
    statement_datasets_dir: Path = Path('.local/annual-reports/statement-snapshots')
    annual_reports_dir: Path = Path('.local/annual-reports')
    ocr_python: Path | None = None
    ocr_models_dir: Path | None = None
    ocr_timeout_seconds: int = 180


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
