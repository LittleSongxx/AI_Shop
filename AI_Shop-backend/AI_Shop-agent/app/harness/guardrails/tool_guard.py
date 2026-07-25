READ_TOOLS = frozenset({
    "SEARCH_PRODUCTS",
    "QUERY_ORDERS",
    "GET_PRODUCT_DETAIL",
    "QUERY_LOGISTICS",
    "QUERY_COMMENT",
    "QUERY_USER_COUPONS",
})

WRITE_TOOLS = frozenset({
    "PROPOSE_REFUND",
    "PROPOSE_CONFIRM_RECEIPT",
    "PROPOSE_PRODUCT_REVIEW",
    "PROPOSE_RECOMMENT",
})

ALL_ALLOWED_TOOLS = READ_TOOLS | WRITE_TOOLS

class ToolGuardrail:

    def is_allowed(self, tool_name: str) -> bool:

        return tool_name in ALL_ALLOWED_TOOLS

    def is_write_tool(self, tool_name: str) -> bool:

        return tool_name in WRITE_TOOLS

    def validate_tool_args(self, tool_name: str, args: dict, user_id: str) -> bool:

        if "userId" in args and args["userId"] != user_id:
            return False
        return True
