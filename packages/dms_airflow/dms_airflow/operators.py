"""DAG steps. Each operator takes an Airflow connection id, not a secret.

``DmsBenchmarkOperator`` fails when the report's ``wrong`` count is above 0.
``DmsSemanticSyncOperator`` stays NotImplemented until FEAT-SEMANTIC-IMPORT-01
merges. Airflow is optional: without the package the classes still run
``execute`` so the WRONG check is tested in CI.
"""

from __future__ import annotations

from typing import Any

from dms_airflow.bench import fail_if_wrong
from dms_airflow.hook import DmsHook

try:
    from airflow.models import BaseOperator
except ImportError:  # pragma: no cover - optional extra
    BaseOperator = object  # type: ignore[misc, assignment]


def _init(operator: Any, *, task_id: str, **kwargs: Any) -> None:
    if BaseOperator is object:
        operator.task_id = task_id
        return
    BaseOperator.__init__(operator, task_id=task_id, **kwargs)


class DmsAskOperator(BaseOperator):
    template_fields = ("space_id", "question")

    def __init__(
        self,
        *,
        task_id: str,
        conn_id: str,
        space_id: str,
        question: str,
        **kwargs: Any,
    ) -> None:
        self.conn_id = conn_id
        self.space_id = space_id
        self.question = question
        _init(self, task_id=task_id, **kwargs)

    def execute(self, context: Any) -> dict[str, Any]:
        del context
        client = DmsHook(conn_id=self.conn_id).get_client()
        try:
            return client.ask(space_id=self.space_id, question=self.question)
        finally:
            client.close()


class DmsBenchmarkOperator(BaseOperator):
    template_fields = ("space_id",)

    def __init__(self, *, task_id: str, conn_id: str, space_id: str, **kwargs: Any) -> None:
        self.conn_id = conn_id
        self.space_id = space_id
        _init(self, task_id=task_id, **kwargs)

    def execute(self, context: Any) -> dict[str, Any]:
        del context
        client = DmsHook(conn_id=self.conn_id).get_client()
        try:
            report = client.benchmark_run(space_id=self.space_id)
        finally:
            client.close()
        fail_if_wrong(report)
        return report


class DmsSemanticSyncOperator(BaseOperator):
    template_fields = ("space_id", "osi_path")

    def __init__(
        self,
        *,
        task_id: str,
        conn_id: str,
        space_id: str,
        osi_path: str = "target/osi_document.json",
        **kwargs: Any,
    ) -> None:
        self.conn_id = conn_id
        self.space_id = space_id
        self.osi_path = osi_path
        _init(self, task_id=task_id, **kwargs)

    def execute(self, context: Any) -> None:
        del context
        raise NotImplementedError(
            "dbt sync is not implemented until FEAT-SEMANTIC-IMPORT-01 is merged"
        )
