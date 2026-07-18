from app.graph.runner import run_agent_graph

class AgentEngine:

    async def assistant_answer(self, agent_msg: dict) -> None:

        await run_agent_graph(agent_msg)

agent_engine = AgentEngine()
