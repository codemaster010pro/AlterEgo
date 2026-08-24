from typing import TypedDict,Annotated,Optional,Any
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from custom_def import merge_dict,overwrite_append_list
from pydantic import Field,BaseModel
from typing_extensions import Literal

class AlterEgo(TypedDict):
    messages: Annotated[list[BaseMessage],add_messages]
    preferences: Annotated[dict[str,Any],merge_dict]
    tool_output:Annotated[list[dict],overwrite_append_list]
    next_node:Optional[str]
    tts_text:Optional[str]
    stt_text:Optional[str]
    
    
class route(BaseModel):
    route: Literal["software","mentor","upgrade"] = Field(description="Target destination engine for the query.")
    reason:str = Field(description="very short(1 sentence) explaination of how this path was best for this and selected.")
    
