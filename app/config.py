from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 纯文本模型
    text_llm_base_url: str = ""
    text_llm_api_key: str = ""
    text_llm_model: str = ""

    # 图片问答模型
    vision_llm_base_url: str = ""
    vision_llm_api_key: str = ""
    vision_llm_model: str = ""

    model_config = {"env_file": ".env"}


settings = Settings()
