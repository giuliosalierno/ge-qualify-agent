"""Deterministic A2UI form emitter for Phase 0 spike tasks 0.6 and 0.7.

This agent contains no LLM. Every A2UI message is built in Python, which is
what the production design calls for (decision D2). That makes it a direct
proof of spike task 0.7, and a clean harness for task 0.6.

Task 0.6 — does `sendDataModel: true` echo?
    The surface is created with `sendDataModel: true`. Per the A2UI v0.9 spec
    the renderer should then attach the surface's full data model to the
    metadata of every message it sends back. This module logs the complete
    inbound message on every turn so we can see whether that actually happens
    in Gemini Enterprise.

Task 0.7 — can we bypass the LLM?
    If this renders in GE, yes.
"""

from typing import Any

from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2ui.a2a.extension import get_a2ui_agent_extension

# A2UI protocol version. Note the SDK constant is "0.9" while the value that
# travels on the wire inside each message is "v0.9". They are deliberately
# different; do not unify them.
A2UI_VERSION = "0.9"
WIRE_VERSION = "v0.9"

# The Gemini Enterprise composite catalog: Material + basic + GE custom
# components. GE advertises this ID, so the agent card must claim it.
GE_COMPOSITE_CATALOG_ID = (
    "https://www.gstatic.com/vertexaisearch/a2ui/v0_9/"
    "gemini_enterprise_composite_catalog.json"
)

SURFACE_ID = "echo-probe"

# Action names the renderer can send back to us.
ACTION_SUBMIT = "spike_submit"


def build_agent_card(base_url: str) -> AgentCard:
    """Builds the A2A agent card advertising A2UI v0.9 support."""
    extension = get_a2ui_agent_extension(
        A2UI_VERSION,
        False,  # accepts_inline_catalogs — we only use the GE catalog by ID
        [GE_COMPOSITE_CATALOG_ID],
    )

    skill = AgentSkill(
        id="echo_probe",
        name="A2UI sendDataModel echo probe",
        description=(
            "Renders a two-field form and reports back exactly what the client"
            " sent, so we can verify whether in-progress form edits reach the"
            " agent without a submit."
        ),
        tags=["a2ui", "spike", "diagnostic"],
        examples=["start", "show the form"],
    )

    return AgentCard(
        name="A2UI Echo Probe",
        description=(
            "Phase 0 diagnostic agent for the GE qualification project. Emits a"
            " deterministic A2UI form and logs all inbound client state."
        ),
        url=base_url,
        version="0.1.0",
        default_input_modes=["text", "text/plain"],
        default_output_modes=["text", "text/plain"],
        capabilities=AgentCapabilities(streaming=True, extensions=[extension]),
        skills=[skill],
    )


def build_form_messages() -> list[dict[str, Any]]:
    """Builds the A2UI message sequence for the probe form.

    Deliberately minimal: a plain Column root rather than the GE `Canvas`
    side panel. We already know Canvas renders (the reference demo uses it).
    Keeping this simple means a failure here points at `sendDataModel` and
    not at component choice.
    """
    create_surface = {
        "version": WIRE_VERSION,
        "createSurface": {
            "surfaceId": SURFACE_ID,
            "catalogId": GE_COMPOSITE_CATALOG_ID,
            # The flag under test.
            "sendDataModel": True,
        },
    }

    update_components = {
        "version": WIRE_VERSION,
        "updateComponents": {
            "surfaceId": SURFACE_ID,
            "components": [
                {
                    "id": "root",
                    "component": "Column",
                    "align": "stretch",
                    "children": ["title", "hint", "account", "users", "submit"],
                },
                {
                    "id": "title",
                    "component": "Text",
                    "text": "sendDataModel echo probe",
                    "variant": "h3",
                },
                {
                    "id": "hint",
                    "component": "Text",
                    "text": (
                        "Edit a field, then send any chat message WITHOUT"
                        " clicking Submit. We are checking whether your edit"
                        " reaches the agent on its own."
                    ),
                },
                {
                    "id": "account",
                    "component": "TextField",
                    "label": "Account name",
                    "value": {"path": "/form/account"},
                },
                {
                    "id": "users",
                    "component": "TextField",
                    "label": "Number of users",
                    "variant": "number",
                    "value": {"path": "/form/users"},
                },
                # A Button takes a child component id for its label, per the
                # v0.9 spec examples — not a `label` property.
                {"id": "submit-text", "component": "Text", "text": "Submit"},
                {
                    "id": "submit",
                    "component": "Button",
                    "child": "submit-text",
                    "action": {
                        "event": {
                            "name": ACTION_SUBMIT,
                            # Binding the whole /form object means we receive
                            # every field without enumerating them.
                            "context": {"data": {"path": "/form"}},
                        }
                    },
                },
            ],
        },
    }

    update_data_model = {
        "version": WIRE_VERSION,
        "updateDataModel": {
            "surfaceId": SURFACE_ID,
            "value": {"form": {"account": "", "users": ""}},
        },
    }

    return [create_surface, update_components, update_data_model]


def build_patch(path: str, value: Any) -> dict[str, Any]:
    """Builds a single-field updateDataModel patch.

    This is the mechanic the living form depends on: the agent writes one
    field without resending the component tree.
    """
    return {
        "version": WIRE_VERSION,
        "updateDataModel": {
            "surfaceId": SURFACE_ID,
            "path": path,
            "value": value,
        },
    }
