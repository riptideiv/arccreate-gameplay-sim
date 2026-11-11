"""Contact-pinned finger paths shared with the touchpoint planner."""

import sys
from pathlib import Path

# Blender's embedded Python does not use the workbench's virtual environment.
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor/arc-hand-order/src"))
from arc_hand_order.planning.hand_travel import RATE, home_position, positions, settle_frame, travel
