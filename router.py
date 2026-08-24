from schema import route, AlterEgo
from langchain.messages import SystemMessage, HumanMessage, AIMessage
from model import llm

router = llm.with_structured_output(route)

def llm_call_router(state: AlterEgo):
    """Route the input to the appropriate node"""

    # Run the augmented LLM with structured output to serve as routing logic
    next_node = router.invoke(
        [
            SystemMessage(
                content="""You are the Intent Routing Engine for Edith, an autonomous AI assistant.
                        Your sole job is to analyze the latest user input and classify it into exactly one target execution engine.

                        ### TARGET ENGINES

                        1. `software`:
                        - Select this for ANY software, local machine, execution, or laptop commands.
                        - Examples: Writing code, debugging scripts, running terminal/bash commands, modifying files, inspecting local system state, git operations, or running local automated tools.

                        2. `mentor`:
                        - Select this for high-level advice, learning concepts, active mentorship, conceptual explanations, or decision-making.
                        - Examples: Explaining software architecture, studying economics, career advice, high school planning, SAT strategy, or active first-principles learning.

                        3. `upgrade`:
                        - Select this ONLY when the user asks about Edith's system health, error logs, or upgrading/modifying Edith's own capabilities.
                        - Examples: "What errors did you encounter today?", "Show me failed runs", "Upgrade your terminal tool", or "Refactor your internal graph."

                        ### RULES
                        - Rely ONLY on the user's explicit intent in the message.
                        - Do NOT answer the user's question or generate any conversational text.
                        - Select the single best matching route enum.
                        - Output MUST conform strictly to the required structured output schema.
                        """
            ),
            HumanMessage(content=state["stt_text"]),
        ]
    )

    return {"next_node": next_node.route,
            "tool_output": []}

def node_3b(state: AlterEgo):
    current_preferences = state["preferences",{}]
    
    system_prompt = """You are the Strategic Mentor module for Edith.
                    Your goal is to guide the user through complex problem-solving using first principles, crisp mental models, and Socratic questioning.

                    ### DIRECTIVES:
                    - Do NOT write boilerplate implementation code unless specifically requested; focus on architecture, logic, and trade-offs.
                    - Respect long-term constraints and rules found in `my_preference`.
                    - Keep explanations clear, scannable, and direct. Avoid unnecessary conversational fluff.
                    - End complex conceptual explanations with ONE sharp follow-up question to test understanding or refine the design.
                    """
    
    system_content = f"{system_prompt}\n\nUser Preferences: {current_preferences}"
    messages_payload = [SystemMessage(content=system_content)]
    
    if state.get("messages"):
        messages_payload.extend(state["messages"])
    else:
        messages_payload.append(HumanMessage(content=state["stt_text"]))
    
    response = llm.invoke(messages_payload)
    
    return {"messages": [AIMessage(content=response.content)],
            "tts_text": response.content}
     
     
     
    


    