"""Bundled tool schemas and validation for AG Studio AI Assistant.

Ensures client-supplied tool definitions strictly match AG Studio's official
tool schemas in name and parameters schema, rejecting unrecognized, extra,
or modified tool schemas with HTTP 400.
"""

from __future__ import annotations

from typing import Any

BUNDLED_STUDIO_TOOLS: dict[str, dict[str, Any]] = {
    "view_schema": {
        "name": "view_schema",
        "description": "View the available data schema - fieldsets and their fields with types. Always call this before creating widgets or writing queries so you know which field IDs exist.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "view_report": {
        "name": "view_report",
        "description": "List the report's pages, with each page's id, how many widgets and page filters it holds, and which page is currently selected. Call this before working on a page so you know which pages exist.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "view_page": {
        "name": "view_page",
        "description": "View the current page state: widget positions, page layout config, active page filters, layout/filter issues, and per-widget health summaries.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "view_widget": {
        "name": "view_widget",
        "description": "View a widget's current config, size, filters, and issues. Returns widgetType, xSpan/ySpan, config, filters, and issues.",
        "parameters": {
            "type": "object",
            "properties": {
                "widgetId": {"type": "string"},
            },
            "required": ["widgetId"],
        },
    },
    "execute_query": {
        "name": "execute_query",
        "description": "Execute a query against the data source.\nTwo modes:\n- aggregation: group by dimensions with aggregated measures (sum, avg, count, etc.)\n- projection: return raw rows with selected columns (no aggregation)\n\nSupports joins, filters, sorting, and pagination.\n\nAliases ('as'): every 'as' value in the query must be unique across dimensions,\nmeasures, projections, and windows \u2014 they share one output namespace. Reusing a\nname (e.g. setting 'as: \"total_revenue\"' on both a dimension and a measure) is\nrejected with validationAliasCollision; pick distinct names per output.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "object"},
            },
            "required": ["query"],
        },
    },
    "add_widget": {
        "name": "add_widget",
        "description": "Add a widget to the grid. Position is 0-indexed.",
        "parameters": {
            "type": "object",
            "properties": {
                "widgetId": {"type": "string"},
                "type": {"type": "string"},
                "xTrack": {"type": "number"},
                "yTrack": {"type": "number"},
                "xSpan": {"type": "number"},
                "ySpan": {"type": "number"},
            },
            "required": ["widgetId", "type", "xTrack", "yTrack", "xSpan", "ySpan"],
        },
    },
    "position_widget": {
        "name": "position_widget",
        "description": "Move and/or resize a widget on the grid. All position fields are optional - omitted fields preserve current values. 0-indexed.",
        "parameters": {
            "type": "object",
            "properties": {
                "widgetId": {"type": "string"},
                "xTrack": {"type": "number"},
                "yTrack": {"type": "number"},
                "xSpan": {"type": "number"},
                "ySpan": {"type": "number"},
            },
            "required": ["widgetId"],
        },
    },
    "remove_widget": {
        "name": "remove_widget",
        "description": "Permanently remove a widget from the page.",
        "parameters": {
            "type": "object",
            "properties": {
                "widgetId": {"type": "string"},
            },
            "required": ["widgetId"],
        },
    },
    "add_page_filter": {
        "name": "add_page_filter",
        "description": "Add a single page-level filter (appended to the page filter list).\nSupports comparison, text, set (isIn), null/blank, relative-date (inRelativeRange), and rank\noperators - see the field type for the available set. For a top/bottom-N filter, use operator \"rank\"\non the numeric field to rank by, with value [topN, bottomN] (null for the unused bound).",
        "parameters": {
            "type": "object",
            "properties": {
                "filter": {"type": "object"},
            },
            "required": ["filter"],
        },
    },
    "remove_page_filter": {
        "name": "remove_page_filter",
        "description": "Remove a page filter by its index in the page's filter list (0-based). Call view_page first to see the current filters and their order.",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer"},
            },
            "required": ["index"],
        },
    },
    "add_widget_filter": {
        "name": "add_widget_filter",
        "description": "Add a single widget-level filter.\nSupports comparison, text, set (isIn), null/blank, relative-date (inRelativeRange), and rank\noperators - see the field type for the available set. For a top/bottom-N filter, use operator \"rank\"\non the numeric field to rank by, with value [topN, bottomN] (null for the unused bound).",
        "parameters": {
            "type": "object",
            "properties": {
                "widgetId": {"type": "string"},
                "filter": {"type": "object"},
            },
            "required": ["widgetId", "filter"],
        },
    },
    "remove_widget_filter": {
        "name": "remove_widget_filter",
        "description": "Remove a widget filter by its index in the widget's filter list. Call view_widget first to see the current filters and their order; the index is the position in that list (0-based).",
        "parameters": {
            "type": "object",
            "properties": {
                "widgetId": {"type": "string"},
                "index": {"type": "integer"},
            },
            "required": ["widgetId", "index"],
        },
    },
    "view_plan": {
        "name": "view_plan",
        "description": "View the current plan with status annotations, or null if no plan exists.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "create_plan": {
        "name": "create_plan",
        "description": "Create a structured plan for dashboard modifications. Contains a proportional layout tree, widget deltas (add/modify/remove), and optional page filter deltas. Widget IDs in the plan become actual dashboard widget IDs. Replaces any existing plan.",
        "parameters": {
            "type": "object",
            "properties": {
                "plan": {"type": "object"},
            },
            "required": ["plan"],
        },
    },
    "update_plan": {
        "name": "update_plan",
        "description": "Update plan widget and page filter statuses. Call after the Page agent places widgets or Widget agents configure them.",
        "parameters": {
            "type": "object",
            "properties": {
                "widgetUpdates": {"type": "array"},
                "filterUpdates": {"type": "array"},
            },
        },
    },
    "clear_plan": {
        "name": "clear_plan",
        "description": "Remove the active plan. Idempotent - no-op if no plan exists. Placed widgets and applied filters remain.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "rename_thread": {
        "name": "rename_thread",
        "description": "Name the current conversation. Call this as soon as you know what the conversation is about, with a short descriptive name for the work itself. Call it again if the subject changes.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The new name for the current conversation. Keep it short and descriptive.",
                },
            },
            "required": ["name"],
        },
    },
    "delegate_to": {
        "name": "delegate_to",
        "description": "Delegate a subtask to a specialised sub-agent, which runs it and returns a summary. You retain control of the conversation. Pick the target agent and give it a concise task.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "task": {"type": "string"},
            },
            "required": ["target", "task"],
        },
    },
    "complete_task": {
        "name": "complete_task",
        "description": "Call this tool when you have completed your task or need to report results. You MUST call this tool to signal completion - do not respond with text alone.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
            },
            "required": ["summary"],
        },
    },
}


def validate_studio_tools(client_tools: Any) -> tuple[bool, str | None]:
    """Validate that client-supplied tools match the bundled Studio tools EXACTLY.

    Returns:
        (is_valid: bool, error_message: str | None)
    """
    if client_tools is None:
        return True, None
    if not isinstance(client_tools, list):
        return False, "The 'tools' field must be an array of tool schema objects."

    for idx, tool in enumerate(client_tools):
        if not isinstance(tool, dict):
            return False, f"Tool at index {idx} is not an object."
        name = tool.get("name")
        if not name or not isinstance(name, str):
            return False, f"Tool at index {idx} is missing a valid 'name'."
        if name not in BUNDLED_STUDIO_TOOLS:
            return False, f"Unrecognized or extra tool: {name}"

        expected_tool = BUNDLED_STUDIO_TOOLS[name]
        expected_params = expected_tool.get("parameters", {})
        client_params = tool.get("parameters", {})

        if client_params != expected_params:
            return False, f"Tool schema mismatch for tool: {name}"

    return True, None

