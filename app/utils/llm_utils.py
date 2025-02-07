import tiktoken
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
import traceback
from ddtrace import tracer

log = get_logger(__name__)

@tracer.wrap(name="dd_trace.num_tokens_from_string",service="degreed-coach-builder")
def num_tokens_from_string(string: str, encoding_name: str = "cl100k_base") -> int:
    """
    Returns the number of tokens in a text string.

    Args:
        string (str): The input text string.
        encoding_name (str): The name of the encoding to use. Default is "cl100k_base".

    Returns:
        int: The number of tokens in the input string.
    """
    encoding = tiktoken.get_encoding(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens

@tracer.wrap(name="dd_trace.num_tokens_from_messages",service="degreed-coach-builder")
def num_tokens_from_messages(messages, model="gpt-4-0613"):
    """
    Return the number of tokens used by a list of messages.

    Args:
        messages (list): A list of message dictionaries.
        model (str): The model name to use for encoding. Default is "gpt-4-0613".

    Returns:
        int: The total number of tokens used by the list of messages.
    """
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        log_warn(log, "Model not found. Using cl100k_base encoding.")
        encoding = tiktoken.get_encoding("cl100k_base")
    except Exception as e:
        log_error(log, "An error occurred while getting encoding for model: %s", str(e))
        log_debug(log, traceback.format_exc())
        raise

    if model in {
        "gpt-3.5-turbo-0613",
        "gpt-3.5-turbo-16k-0613",
        "gpt-4-0314",
        "gpt-4-32k-0314",
        "gpt-4-0613",
        "gpt-4-32k-0613",
    }:
        tokens_per_message = 3
        tokens_per_name = 1
    elif model == "gpt-3.5-turbo-0301":
        tokens_per_message = 4  # every message follows <|start|>{role/name}\n{content}<|end|>\n
        tokens_per_name = -1  # if there's a name, the role is omitted
    elif "gpt-3.5-turbo" in model:
        log_warn(log, "gpt-3.5-turbo may update over time. Returning num tokens assuming gpt-3.5-turbo-0613.")
        return num_tokens_from_messages(messages, model="gpt-3.5-turbo-0613")
    elif "gpt-4" in model:
        log_warn(log, "gpt-4 may update over time. Returning num tokens assuming gpt-4-0613.")
        return num_tokens_from_messages(messages, model="gpt-4-0613")
    else:
        raise NotImplementedError(
            f"num_tokens_from_messages() is not implemented for model {model}. See https://github.com/openai/openai-python/blob/main/chatml.md for information on how messages are converted to tokens."
        )

    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        for key, value in message.items():
            num_tokens += len(encoding.encode(value))
            if key == "name":
                num_tokens += tokens_per_name
    num_tokens += 3  # every reply is primed with <|start|>assistant<|message|>
    return num_tokens
