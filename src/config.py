from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8")

    odata_url: str
    odata_username: str
    odata_password: str
    dropbox_refresh_token: str
    dropbox_app_key: str
    dropbox_app_secret: str
    images_directory: str
    csv_files_directory: str
    tilda_email: str
    tilda_password: str
    tilda_project_id: str
    selenium_timeout: int
    selenium_file_uploading_timeout: int


settings = Settings()
