"""Arcaea hand-order planner: a multi-finger DP that gives each note of a chart a hand, a finger and a touch path.

Public entry points: ``arc_hand_order.api`` (load a chart and a device, plan with a preset, write the plan JSON) and
``python -m arc_hand_order`` (the same from the command line). See README.md and docs/FORMAT.md.
"""

__version__ = "1.0.1"
