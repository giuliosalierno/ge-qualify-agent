"""Interactive Gemini Enterprise views built from A2UI components.

Each module builds one side-panel view (``Canvas``) from data the agent
already has: scored portfolio evaluations, a finished ``UseCaseRecord``.
Every component used here was seen rendering in GE by the ``probe canvas``
diagnostic on 2026-10-01 (see ``catalog.GE_RENDER_VERIFIED``).

Views are switched off with ``INTERACTIVE_VIEWS=0``, which brings back the
plain markdown replies, in case a GE release stops rendering them.
"""
