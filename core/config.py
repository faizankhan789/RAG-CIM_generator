from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    signature_header_name: str = "X-Signature"
    signature_header_value: str = "secret-token"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
