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

    # 阿里云图像搜索配置
    aliyun_image_search_access_key_id: str = ""
    aliyun_image_search_access_key_secret: str = ""
    aliyun_image_search_instance_name: str = ""
    aliyun_image_search_region_id: str = ""
    aliyun_image_search_endpoint: str = ""

    model_config = {"env_file": ".env"}


settings = Settings()
