# Decisiones técnicas y de producto

Cada decisión indica alternativas consideradas y la justificación.

## 1. Disponibilidad de la información en el tiempo

**D1. Modelo bitemporal de la bandeja (fecha del dato + fecha de llegada).**
Cada archivo vive en `data/inbox/<fuente>/arrival=<fecha_llegada>/<fuente>_<fecha_dato>_v<N>.csv`.
Al procesar la fecha lógica D, el flujo solo ve archivos con `arrival <= D` (`ingest.as_of`).
- *Alternativas*: usar la fecha de modificación del archivo (no determinista, se pierde al copiar/clonar);
  usar solo la fecha del dato (no permite representar llegadas tardías ni correcciones).
- *Justificación*: la llegada explícita en la ruta hace que el flujo sea reproducible y que el backfill vea
  exactamente lo que se habría visto ese día.

**D2. Semántica del corte.** El pronóstico del corte C (lunes) se emite al inicio del día C y cubre
C … C+13. Usa ventas hasta C−1 (si llegaron), transacciones hasta ~C−3 y petróleo hasta el viernes previo.
El `meta.json` de cada pronóstico publicado guarda la *marca de agua* (último dato disponible) por fuente.

**D3. Retrasos simulados distintos por fuente** (`config/params.yaml > simulation`): ventas +1 día,
transacciones +3 días, petróleo en lote semanal (lunes), feriados publicados 30 días antes, tiendas estáticas
en la carga histórica. Así el modelo debe convivir con fuentes desfasadas.

**D4. Features robustas a datos tardíos.** Todas las ventanas usan `nanmean` sobre lo disponible; si una
fuente se atrasa, la ventana tiene menos observaciones en lugar de fallar o imputar con datos futuros.
Una prueba (`test_features_do_not_use_future_data`) altera las ventas futuras y verifica que las
features no cambian.

**D5. Promociones futuras no se usan.** En Kaggle `onpromotion` futuro existe, pero en nuestra bandeja llega
junto con las ventas (+1 día); usarlo sería fuga. Se usa la intensidad promocional reciente.

**D6. Datos históricos corregidos para entrenar.** Al entrenar en el corte C se usan las ventas *más
recientes conocidas a C* (incluyendo correcciones ya llegadas), no la versión que existía en cada corte
pasado. *Alternativa*: reconstruir cada ejemplo con su propio as-of (más fiel, pero ~10x más costoso).
Se acepta porque las features de cada ejemplo siguen sin mirar al futuro relativo a su corte.

## 2. Validación de los datos

**D7. Validación por archivo antes del lago**: esquema, fechas interpretables y coherentes con el nombre,
llaves no nulas ni duplicadas, ventas/promociones/transacciones no negativas, tiendas y familias conocidas.
Archivo inválido → `data/quarantine/<fuente>/<fecha>_v<N>.json` con el motivo; **no** llega al lago.
- *Alternativa*: fallar el DAG completo. Se descartó: un archivo malo de transacciones no debe impedir el
  pronóstico; la ausencia se trata como una fuente atrasada (D4). El DAG reporta la cuarentena en el log.

**D8. Situaciones esperadas que no son errores**: precio de petróleo vacío (feriado en EE. UU.), días sin
archivo (25 de diciembre), tiendas con cero ventas, fuentes que llegan tarde. Se manejan sin excepción.

## 3. Manejo de correcciones

**D9. Una corrección es una versión nueva del mismo `fecha_dato`**, con llegada posterior. Gana la versión
con (llegada, versión) más alta *disponible al corte*. La bandeja nunca se modifica.

**D10. Los pronósticos publicados son inmutables respecto de correcciones posteriores.** Re-ejecutar un
corte pasado reproduce el mismo archivo, porque usa el as-of de ese corte (prueba
`test_weekly_forecast_is_reproducible_and_idempotent`). Lo que sí se reprocesa con correcciones es:
el seguimiento del desempeño (siempre compara contra la versión más reciente de las ventas reales), los
cortes futuros y los reentrenamientos.

## 4. Orquestación

**D11. Cuatro DAGs** (`dags/`): `lab1_bootstrap` (manual), `lab1_ingesta_validacion` (diario),
`lab1_pronostico_pedido` (semanal, lunes) y `lab1_seguimiento` (diario). El semanal vuelve a correr la
ingesta del día (idempotente) para no depender de sensores entre DAGs.

**D12. Idempotencia**: cada tarea escribe salidas con nombre determinista por fecha y las sobrescribe con
escritura atómica (`tmp` + `os.replace`). El calendario de reentrenamiento depende solo de la fecha
(cada 4 semanas desde el primer corte), así el backfill reentrena en las mismas fechas.

**D13. Backfill**: `catchup=True` con `start_date`/`end_date` tomados de `params.yaml` y `max_active_runs=1`
para respetar el orden temporal.

**D14. Airflow 3 con LocalExecutor + Postgres** (sin Celery/Redis): suficiente para un nodo, menos
servicios y memoria. Imagen propia con `lightgbm` y `libgomp1`.

## 5. Modelado

**D15. Horizonte 14 días, corte semanal (lunes), pedido que cubre 7 días.** El horizonte de 14 días deja
margen para el siguiente pedido y para el tiempo de entrega.

**D16. Baseline**: naive estacional — cuantiles empíricos de los últimos 8 mismos días de la semana.

**D17. Modelo ML**: LightGBM global (todas las series), un modelo por cuantil (0.1, 0.25, 0.5, 0.75, 0.9)
sobre `log1p(ventas)`; los cuantiles son invariantes a transformaciones monótonas. Estrategia directa
multi-horizonte (h como feature).
- *Deep learning (DeepAR, TFT)*: descartado. Con ~1.8k series y horizonte corto, los GBM son
  competitivos, entrenan en minutos en CPU y son fáciles de reproducir; DL requeriría GPU y más tiempo
  de ajuste sin beneficio esperado claro.

**D18. Validación temporal (rolling origin)**: 3 cortes separados 2 semanas, mismos cortes y mismas filas
para baseline y LightGBM; cada modelo entrena solo con objetivos anteriores al corte evaluado.

**D19. Métricas**: técnicas — WAPE (P50), RMSLE, pinball promedio, cobertura del intervalo 10–90;
de negocio — costo total = Σ cu·faltante + co·sobrante usando el pedido derivado de los costos.

**D20. Pedido (newsvendor)**: cantidad diaria = cuantil crítico `cu/(cu+co)` interpolado entre los
cuantiles pronosticados (perecederos 1/(1+1.5)=0.40; resto 2/(2+0.5)=0.80). Pedido semanal = suma de
los 7 primeros días. Sin datos de inventario, el pedido equivale a la demanda a cubrir (supuesto
explícito). Cambiar costos en `params.yaml` cambia el pedido sin tocar código.

**D21. Criterio de reemplazo del modelo vigente**: el retador reemplaza al vigente si reduce el costo de
negocio en el backtest ≥ 2 % **y** su WAPE no empeora más de 0.01. Si vigente y retador son del mismo
método (reentrenamiento programado), el nuevo se acepta solo si sigue superando al baseline con el mismo
criterio; si no, se mantiene el vigente. Cada decisión queda en `models/<id>/meta.json`.

## 6. Versionado

**D22. DVC** versiona: `kaggle/` (entrada original), `data/history` y `data/inbox` (salidas de la etapa
`bootstrap` en `dvc.yaml`), `data/lake` y `data/features` (procesados), `models/` y `data/forecasts/`
(publicados). Los reportes pequeños (`reports/*.csv`) van en Git como evidencia legible.

**D23. Remoto DVC local** (`D:/dvc-remote-lab1`) por restricción de tiempo; para un equipo se cambiaría a
S3/DagsHub con `dvc remote modify`. El producto completo también se reconstruye desde los CSV de Kaggle
(el flujo es determinista).

**D24. Sin Docker local**: el equipo de desarrollo no tenía espacio para Docker Desktop; Airflow se validó
en GitHub Codespaces con el mismo `docker-compose.yaml` que usa el equipo docente.
