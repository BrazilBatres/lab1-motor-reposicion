"""Diario: compara los pronósticos publicados con las ventas reales (incluye correcciones)."""
from airflow.sdk import dag, task

from _common import DEFAULT_ARGS, END, START


@dag(dag_id="lab1_seguimiento", schedule="0 12 * * *", start_date=START, end_date=END,
     catchup=True, max_active_runs=1, default_args=DEFAULT_ARGS, tags=["lab1"], doc_md=__doc__)
def seguimiento():
    @task
    def ingerir(ds=None):
        from lab1 import ingest
        return ingest.ingest(ds)

    @task
    def medir_desempeno(ds=None):
        from lab1 import product
        return product.monitor_summary(product.monitor(ds))

    ingerir() >> medir_desempeno()


seguimiento()
