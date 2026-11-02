# Reglas del proyecto (contexto para agentes de programación)

Producto: motor de reposición de inventario (pronóstico por cuantiles + pedido sugerido + seguimiento)
con Git + DVC + Airflow 3. Leer `README.md` y `DECISIONES.md` antes de cambiar comportamiento.

## Principios no negociables
1. **Sin fuga de información**: toda predicción para el corte C usa solo `ingest.as_of(C)`
   (historia + archivos de la bandeja con `arrival <= C`). Nunca leer `kaggle/` ni la bandeja directo
   desde features/modelos.
2. **La bandeja (`data/inbox`) es de solo lectura** para el flujo. Solo `simulate.py` la crea.
3. **Idempotencia**: cada tarea escribe salidas deterministas por fecha (sobrescribe, escritura atómica).
   Nada de appends ni de estado que dependa de cuántas veces se corrió.
4. **Datos inválidos → cuarentena** (`data/quarantine`), nunca al lago. Situaciones esperadas
   (petróleo faltante, día sin ventas, fuente atrasada) no deben fallar.
5. **Los DAGs solo orquestan**: la lógica vive en `src/lab1/` y se prueba con pytest.
6. **Costos y parámetros en `config/params.yaml`**: no hardcodear costos, horizonte ni fechas.
7. **Datos y modelos con DVC**, nunca en Git.

## Convenciones
- Código y documentación en español; nombres de funciones en inglés corto.
- Antes de un PR: `python -m pytest -q` debe pasar. Trabajar en ramas; `main` protegida.
- Comando local sin Airflow: `PYTHONPATH=src python -m lab1.cli <bootstrap|ingest|weekly|monitor|backtest> [fecha]`.
