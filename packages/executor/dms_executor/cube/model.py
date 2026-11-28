"""Pydantic cube catalog and the plan the model returns.

Cubes are steward-approved rows. Nothing here is a table of question words.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Agg = Literal["sum", "count", "avg", "min", "max", "count_distinct"]
Cardinality = Literal[
    "many_to_one",
    "one_to_one",
    "one_to_many",
    "many_to_many",
    "TO_MANY",
    "unverified",
]
Grain = Literal["day", "week", "month", "quarter", "year"]
FilterOp = Literal["eq", "neq", "gt", "gte", "lt", "lte", "in"]
Direction = Literal["asc", "desc"]
CubeStatus = Literal["proposed", "approved", "revoked"]

# sqlglot date_trunc units. A plan grain outside this set is not a member.
GRAINS = frozenset({"day", "week", "month", "quarter", "year"})
TO_MANY = frozenset({"TO_MANY", "one_to_many", "many_to_many"})


class CubeMeasure(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    expr: str
    table: str
    agg: Agg
    description: str = ""


class CubeDimension(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    expr: str
    table: str
    description: str = ""


class CubeTimeDimension(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    expr: str
    table: str
    description: str = ""


class CubeJoin(BaseModel):
    model_config = ConfigDict(extra="ignore")
    from_table: str
    from_columns: list[str]
    to_table: str
    to_columns: list[str]
    cardinality: Cardinality


class Cube(BaseModel):
    """One approved (or proposed) cube. ``status`` is proposed until a steward approves."""

    model_config = ConfigDict(extra="ignore")
    name: str
    base_table: str
    measures: list[CubeMeasure] = Field(default_factory=list)
    dimensions: list[CubeDimension] = Field(default_factory=list)
    time_dimensions: list[CubeTimeDimension] = Field(default_factory=list)
    joins: list[CubeJoin] = Field(default_factory=list)
    description: str = ""
    status: CubeStatus = "proposed"


class PlanTime(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    grain: str


class PlanFilter(BaseModel):
    model_config = ConfigDict(extra="ignore")
    member: str
    op: str
    value: Any = None


class PlanOrder(BaseModel):
    model_config = ConfigDict(extra="ignore")
    member: str
    direction: str = "asc"


class CubePlan(BaseModel):
    """Structured plan. The model names members. It does not send SQL."""

    model_config = ConfigDict(extra="ignore")
    cube: str
    measures: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    time_dimension: PlanTime | None = None
    filters: list[PlanFilter] = Field(default_factory=list)
    order: list[PlanOrder] = Field(default_factory=list)
    limit: int | None = None


class ClarifyReading(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = ""
    text: str = ""
