from __future__ import annotations

print("Hi, I'm a module!")

raise Exception(
    "This module-level exception should also not occur during freeze"
)
