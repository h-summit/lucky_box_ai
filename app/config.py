from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置。"""

    port: int = 8000

    # 纯文本模型
    text_llm_base_url: str = ""
    text_llm_api_key: str = ""
    text_llm_model: str = ""

    # 图片问答模型
    vision_llm_base_url: str = ""
    vision_llm_api_key: str = ""
    vision_llm_model: str = ""

    # 百度图片搜索配置
    baidu_image_search_api_key: str = ""
    baidu_image_search_secret_key: str = ""
    baidu_image_search_base_url: str = "https://aip.baidubce.com"
    baidu_image_search_mapping_db_path: str = ".data/baidu_image_index.sqlite3"

    model_config = {"env_file": ".env"}


settings = Settings()
