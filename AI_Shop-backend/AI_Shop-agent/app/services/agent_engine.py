from app.graph.runner import run_agent_graph


class AgentEngine:

    async def assistant_answer(self, agent_msg: dict) -> str:
        """执行一轮 Agent 图，返回 outcome（ok/llm_error/graph_error/cancelled）。"""

        return await run_agent_graph(agent_msg)

agent_engine = AgentEngine()
