"""Local Spark session for pipeline-logic unit tests.

Session-scoped: starting a JVM per test would make this package the slowest thing in the
suite for no benefit — the SQL under test is stateless per call. Each test registers its own
input temp view(s) and drops them via a function-scoped fixture, so tests don't see each
other's data despite sharing the session.

**Rows go through a JSON file, not `spark.createDataFrame(list_of_dicts)`.** The latter
builds an RDD from a Python list and ships it to the JVM via cloudpickle — which segfaults
into a `RecursionError` under this environment's Python 3.14 + pyspark 3.5.9 combination
(too new a Python for that pyspark line's pickle protocol; pyspark 4.x would need Java 17,
not available here). Writing rows to a temp NDJSON file and reading it back with
`spark.read.json(path)` never touches that code path — it's a pure JVM-side file read — so
it sidesteps the incompatibility rather than working around it test-by-test.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from pyspark.sql import DataFrame, SparkSession


@pytest.fixture(scope="session")
def spark() -> Iterator[SparkSession]:
    session = (
        SparkSession.builder.master("local[2]")
        .appName("fleetguard-pipeline-unit-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


@pytest.fixture()
def register(spark: SparkSession, tmp_path: Path):
    """`register(name, rows)` creates a temp view a pipeline SQL statement can read as if
    it were `bronze_x` / `silver_x`. Rows are plain dicts; Spark infers the schema from
    them, which is fine here — these tests are about the transformation logic, not about
    catching a column-type drift in Auto Loader's own explicit schema declaration."""
    created: list[str] = []

    def _register(name: str, rows: list[dict]) -> DataFrame:
        path = tmp_path / f"{name}.json"
        with path.open("w") as f:
            for row in rows:
                f.write(json.dumps(row, default=str) + "\n")
        df = spark.read.json(str(path))
        df.createOrReplaceTempView(name)
        created.append(name)
        return df

    yield _register
    for name in created:
        spark.catalog.dropTempView(name)
