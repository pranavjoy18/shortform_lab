"""shortform_lab: a local talking-head video enhancer.

Takes one short raw talking-head clip and produces a polished vertical
short-form video with a hook, captions, simple overlays, and punch-in zooms.

The pipeline is file-based and local-only: it produces an inspectable
``edit_plan.json`` first, then renders ``final.mp4`` from that plan, so every
creative decision can be reviewed and replaced.
"""

__version__ = "0.1.0"
