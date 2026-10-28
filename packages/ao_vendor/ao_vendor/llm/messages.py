# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stand-ins for the two langchain message types the semantic modules build."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SystemMessage:
    content: str
    role: str = "system"


@dataclass
class HumanMessage:
    content: str
    role: str = "user"
