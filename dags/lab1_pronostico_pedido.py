"""Semanal (lunes): pronóstico por cuantiles y pedido sugerido con la información disponible al corte."""
from airflow.sdk import dag, task

from _common import DEFAULT_ARGS, END, START


@dag(dag_id="lab1_pronostico_pedido", schedule="0 6 * * 1", start_date=START, end_date=END,
     catchup=True, max_active_runs=1, default_args=DEFAULT_ARGS, tags=["lab1"], doc_md=__doc__)
def pronostico():
    @task
    def ingerir(ds=None):
        from lab1 import ingest
        return ingest.ingest(ds)  # idempotente: no importa si el DAG diario ya lo hizo

    @task
    def preparar_features(ds=None):
        from lab1 import product
        return product.prepare(ds)

    @task
    def reentrenar_si_corresponde(ds=None):
        import pandas as pd
        from lab1 import modeling
        if not modeling.retrain_due(pd.Timestamp(ds), modeling.load_params()):
            return {"retrained": False}
        return modeling.train_and_register(ds)

    @task
    def pronosticar_y_pedir(ds=None):
        from lab1 import product
        return product.forecast(ds)

    @task
    def seguimiento(ds=None):
        from lab1 import product
        return product.monitor(ds).to_dict(orient="records")

    ingerir() >> preparar_features() >> reentrenar_si_corresponde() >> pronosticar_y_pedir() >> seguimiento()


pronostico()
