from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "Multi-Agent Autonomous Reasoning Stock Trading System"
    database_url: str = "sqlite:///./trading.db"
    jwt_secret: str = "change-this-secret"
    groq_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"
    initial_cash: float = 100000.0
    paper_trading: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
