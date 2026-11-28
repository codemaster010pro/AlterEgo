from router import llm_call_router,mentor_node,code_writer,decider
from code_checker import code_checker,should_retry
from custom_def import opencoder_worker_node
from executor_node import software_executor
from orchestrator import orchestrator_node
from langgraph.graph import END,START,StateGraph
from schema import AlterEgo

graph = StateGraph(AlterEgo)
graph.add_node("router",llm_call_router)
graph.add_node("mentor",mentor_node)
graph.add_node("orchestrator",orchestrator_node)
graph.add_node("opencoder_worker",code_writer)
graph.add_node("executor",software_executor)
graph.add_node("code_checker",code_checker)

graph.add_edge(START,"router")
graph.add_conditional_edges(
    "router",
    decider,
    {
        "sofware" : "orchestrator",
        "mentor" : "mentor",
        END: END
    }
)
graph.add_edge("mentor",END)
graph.add_edge("orchestrator","opencoder_worker")
graph.add_edge("opencoder_worker","executor")
graph.add_edge("executor","code_checker")
graph.add_conditional_edges(
    "code_checker",
    should_retry,
    {
        "code_checker":"opencoder_worker",
        "complete":END
    }
)

compiled = graph.compile()
print(compiled.get_graph().draw_ascii())