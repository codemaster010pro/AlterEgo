from typing import TypedDict,Annotated,Optional
import operator
from langchain.messages import BaseMessage

class AlterEgo(TypedDict):
    messages: Annotated[list[BaseMessage],operator.add]
    my_preference: dict[str,any]
    tool_output:Optional[dict]
    tts_text:str
    