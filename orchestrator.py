from schema import AlterEgo, orchestration
from model import llm
from langchain_core.prompts import ChatPromptTemplate

def orchestrator_node(state:AlterEgo):
    
    user_input = state['stt_text'] or state["messages"][-1].content
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are the Orchestrator module for Edith, an autonomous AI assistant. Your job is to analyze the user's input and break it down into a list of tasks that can be executed by the appropriate modules. \n\n"),
        ("user", f"User Input: {user_input}\n\nPlease generate one prompt for each task for the worker to do that will help the user achieve their goal conscisely and efficiently.")
    ])

    chain = prompt | llm.with_structured_output(orchestration)
    
    response:orchestration = chain.invoke({"user_input": user_input})

    return {"tool_output": [{"tasks": response.tasks}]}
