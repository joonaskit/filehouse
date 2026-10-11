import os

# UI tests must not open windows on the developer's desktop
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
