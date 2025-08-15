"""
Utilities to fetch info from Hugging Face
"""
from huggingface_hub import HfApi


def get_model_info_from_hf(model_name: str, hf_token: str | None = None):
    """
    Fetches model info from hf
    """
    api = HfApi(token=hf_token)
    try:
        return api.model_info(model_name)
    except Exception as e:
        return None

def get_model_parameters(model_name: str, hf_token: str | None = None):
    model_info = get_model_info_from_hf(model_name, hf_token)
    return model_info