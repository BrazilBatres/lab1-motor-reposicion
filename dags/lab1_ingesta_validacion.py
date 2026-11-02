"""Ingesta diaria: valida los archivos llegados a la bandeja, cuarentena de inválidos y prepara datos."""
from airflow.sdk import dag, task

from _common import DEFAULT_ARGS, END, START


@dag(dag_id="lab1_ingesta_validacion", schedule="0 0 * * *", start_date=START, end_date=END,
     catchup=True, max_active_runs=1, default_args=DEFAULT_ARGS, tags=["lab1"], doc_md=__doc__)
def ingesta():
    @task
    def ingerir_y_validar(ds=None):
        from lab1 import ingest
        return ingest.ingest(ds)

    @task
    def reporte_calidad(report: dict):
        # Archivos inválidos: se detienen en cuarentena (no llegan al lago) y se reportan,
        # sin fallar el flujo, porque el resto de fuentes sigue siendo utilizable.
        for q in report["quarantined"]:
            print(f"CUARENTENA {q['file']}: {q['errors']}")
        return len(report["quarantined"])

    @task
    def preparar_datos(ds=None):
        from lab1 import product
        return product.prepare(ds)

    r = ingerir_y_validar()
    reporte_calidad(r) >> preparar_datos()


ingesta()
