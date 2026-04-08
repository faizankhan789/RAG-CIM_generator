from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Request authentication
    signature_header_name: str = "X-Signature"
    signature_header_value: str = "secret-token"

    # Base directory under which all downloaded batches are stored.
    # Each batch gets its own subdirectory named by a UUID.
    cim_download_dir: str = "/tmp/cim"

    # Maximum number of files downloaded concurrently across all requests.
    cim_max_concurrent_downloads: int = 10

    # Maximum number of files processed concurrently across all requests.
    cim_max_concurrent_processing: int = 5

    # Logging level for the entire application. One of: DEBUG, INFO, WARNING, ERROR.
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
