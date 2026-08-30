"""Trusted AgentCard construction for the Local Assurance Agent."""

from __future__ import annotations

from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from .config import AssuranceConfig
from .version import PACKAGE_VERSION


ASSURANCE_AGENT_NAME = "Local Assurance Agent"


def build_agent_card(config: AssuranceConfig) -> AgentCard:
    return AgentCard(
        name=ASSURANCE_AGENT_NAME,
        description=(
            "对已批准的本地 LTE 数据执行确定性异常扫描、显式确认和只读根因分析。"
        ),
        url=config.public_url,
        version=PACKAGE_VERSION,
        protocol_version="0.3.0",
        preferred_transport="JSONRPC",
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            state_transition_history=True,
        ),
        skills=[
            AgentSkill(
                id="local_assurance_detect",
                name="Detect Local LTE Incidents",
                description="扫描已批准的本地 LTE 数据并返回待确认候选摘要。",
                tags=["assurance", "lte", "detect", "local"],
                examples=["扫描本地 LTE 异常并在我确认后创建 Incident。"],
                input_modes=["application/json"],
                output_modes=["application/json", "text/plain"],
            ),
            AgentSkill(
                id="local_assurance_confirm",
                name="Confirm Local Incident",
                description="验证服务端挑战和快照后，幂等创建或关联 Incident。",
                tags=["assurance", "incident", "confirm", "local"],
                examples=["确认预览中的候选事件。"],
                input_modes=["application/json"],
                output_modes=["application/json", "text/plain"],
            ),
            AgentSkill(
                id="local_assurance_analyze",
                name="Analyze Local Incident",
                description="加载服务端 Incident 快照并执行确定性只读 RCA。",
                tags=["assurance", "rca", "analyze", "local"],
                examples=["分析已创建 Incident 的根因。"],
                input_modes=["application/json"],
                output_modes=["application/json", "text/plain"],
            ),
        ],
    )


# Existing agents use this spelling; keep it public for a predictable migration.
get_agent_card = build_agent_card


__all__ = ["ASSURANCE_AGENT_NAME", "build_agent_card", "get_agent_card"]
