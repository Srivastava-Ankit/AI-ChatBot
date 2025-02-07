from openai import AsyncAzureOpenAI, AzureOpenAI
import os


AZURE_ASYNC_CLIENT = AsyncAzureOpenAI(
    api_key=str(os.getenv("AZURE_GPT_4O_API_KEY")).strip(),
    api_version=str(os.getenv("AZURE_GPT_4O_API_VERSION")).strip(),
    azure_endpoint=str(os.getenv("AZURE_GPT_4O_BASE_URL")).strip()
)



AZURE_CLIENT = AzureOpenAI(
    api_key=str(os.getenv("AZURE_GPT_4O_API_KEY")).strip(),
    api_version=str(os.getenv("AZURE_GPT_4O_API_VERSION")).strip(),
    azure_endpoint=str(os.getenv("AZURE_GPT_4O_BASE_URL")).strip()
)
