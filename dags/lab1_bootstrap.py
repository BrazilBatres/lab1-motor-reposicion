"""Construye la carga histórica y la bandeja simulada a partir de los archivos de Kaggle (manual)."""
from airflow.sdk import dag, task

from lab1.dag_config import DEFAULT_ARGS


@dag(dag_id="lab1_bootstrap", schedule=None, catchup=False, default_args=DEFAULT_ARGS,
     tags=["lab1"], doc_md=__doc__)
def bootstrap():
    @task
    def construir_historia_y_bandeja():
        from lab1 import simulate
        return simulate.run()

    construir_historia_y_bandeja()


bootstrap()
