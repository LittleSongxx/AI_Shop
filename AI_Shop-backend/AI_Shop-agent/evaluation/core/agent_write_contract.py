"""Shared visible/final contracts for local Agent write evaluation."""

from __future__ import annotations

from typing import Any


def cancelable_order_contract(*, confirmed: bool) -> dict[str, Any]:
    assertions = (
        [
            {
                "path": "/components/orders/0/order_status",
                "operator": "equals",
                "value": 4,
            },
            {
                "path": "/orderAudit/orders/0/order_status",
                "operator": "equals",
                "value": 4,
            },
            {
                "path": "/orderAudit/orderCommandLedger/0/status",
                "operator": "equals",
                "value": "COMPLETED",
            },
            {
                "path": "/orderAudit/orderCommandLedger/0/command_type",
                "operator": "equals",
                "value": "AGENT_CANCEL_ORDER",
            },
            {
                "path": "/orderAudit/counts/orders",
                "operator": "equals",
                "value": 1,
            },
            {
                "path": "/orderAudit/counts/orderItems",
                "operator": "equals",
                "value": 1,
            },
            {
                "path": "/orderAudit/counts/orderCommandLedger",
                "operator": "equals",
                "value": 1,
            },
            {
                "path": "/orderAudit/counts/inventory",
                "operator": "equals",
                "value": 1,
            },
            {
                "path": "/orderAudit/counts/stockChangeRecords",
                "operator": "equals",
                "value": 1,
            },
            {
                "path": "/orderAudit/counts/compensationLogs",
                "operator": "equals",
                "value": 0,
            },
            {
                "path": "/orderAudit/inventory/0/stock",
                "operator": "delta",
                "value": 1,
            },
            {
                "path": "/orderAudit/stockChangeRecords/0/change_type",
                "operator": "equals",
                "value": "ORDER_CLOSE_RESTORE",
            },
            {
                "path": "/orderAudit/stockChangeRecords/0/change_amount",
                "operator": "equals",
                "value": 1,
            },
            {
                "path": "/orderAudit/compensationLogs",
                "operator": "unchanged",
            },
        ]
        if confirmed
        else []
    )
    return {
        "stateFixture": {
            "provision": {
                "kind": "CANCELABLE_ORDER_V1",
                "scope": "LOCAL_EVALUATION_ONLY",
            }
        },
        "stateMode": "WRITE_CONFIRMED" if confirmed else "PROPOSE_ONLY",
        "stateAssertions": assertions,
        "confirmationFlow": (
            {
                "proposalTurn": 0,
                "execute": True,
                "repeatConfirm": True,
                "expectedActionToken": True,
                "expectedSuccess": True,
            }
            if confirmed
            else {
                "proposalTurn": 0,
                "execute": False,
                "repeatConfirm": False,
                "expectedActionToken": True,
            }
        ),
    }
