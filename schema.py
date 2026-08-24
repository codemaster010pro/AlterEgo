from typing import TypedDict,Annotated,Optional,Any
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from custom_def import merge_dict,overwrite_append_list

class AlterEgo(TypedDict):
    messages: Annotated[list[BaseMessage],add_messages]
    my_preference: Annotated[dict[str,Any],merge_dict]
    tool_output:Annotated[list[dict],overwrite_append_list]
    next_node:Optional[str]
    tts_text:Optional[str]
    stt_text:Optional[str]
