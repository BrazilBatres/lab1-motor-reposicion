# Motor de Reposición de Inventario — Laboratorio 1 (Product Development)

Producto de ML que, de forma automática y reproducible, transforma datos de ventas que llegan a diario
en un **pronóstico por cuantiles** (14 días, por tienda y familia), un **pedido sugerido** semanal
basado en costos de negocio y un **seguimiento del desempeño** de los pronósticos publicados.
Integra **Git + DVC + Apache Airflow 3** (Docker Compose). Datos: Corporación Favorita (Kaggle).

Las decisiones técnicas y de producto están en [DECISIONES.md](DECISIONES.md).

## Estructura

```
config/params.yaml      # configuración versionada (fechas, horizonte, cuantiles, COSTOS, modelo, política)
src/lab1/               # toda la lógica (los DAGs solo orquestan)
  simulate.py           #   carga histórica + bandeja simulada (sistema de origen)
  ingest.py             #   ingesta, validación, cuarentena y snapshot "as-of"
  features.py           #   features por corte + baseline
  modeling.py           #   LightGBM por cuantiles, backtest temporal, política de campeón, registro
  business.py           #   costos, pedido newsvendor, métricas técnicas y de negocio
  product.py            #   preparación, publicación del pronóstico/pedido, seguimiento
  cli.py                #   ejecución sin Airflow
dags/                   # DAGs de Airflow 3
tests/                  # pytest (corre en cada PR con GitHub Actions)
docker/Dockerfile, docker-compose.yaml
dvc.yaml, *.dvc         # versionado de datos, modelos y pronósticos
reports/                # evidencia: comparación de modelos y seguimiento
```

## Levantar el producto desde cero

Requisitos: Docker (con Docker Compose v2, ≥ 6 GB de RAM para Docker) y Git.

1. Clonar y colocar los archivos de Kaggle **en `kaggle/`** (raíz del repositorio):
   ```bash
   git clone <url-del-repo> lab1 && cd lab1
   mkdir -p kaggle   # copiar aquí train.csv, stores.csv, holidays_events.csv, oil.csv, transactions.csv
   ```
2. Levantar Airflow 3:
   ```bash
   echo "AIRFLOW_UID=$(id -u)" > .env      # en Linux/Codespaces; en Windows/Mac no es necesario
   docker compose up -d --build
   ```
   Interfaz: http://localhost:8080 (sin usuario: `SIMPLE_AUTH_MANAGER_ALL_ADMINS`).
3. En la interfaz (o por CLI), ejecutar el DAG **`lab1_bootstrap`** una vez: crea `data/history`
   (carga histórica hasta `simulation.history_end`) y `data/inbox` (bandeja simulada).
   ```bash
   docker compose exec airflow-scheduler airflow dags trigger lab1_bootstrap
   ```
4. Activar (unpause) los DAGs `lab1_ingesta_validacion`, `lab1_pronostico_pedido` y `lab1_seguimiento`.
   Tienen `catchup=True`: hacen *backfill* automático desde `simulation.inbox_start` hasta
   `simulation.inbox_end + 14 días`, sin intervención manual.
   ```bash
   for d in lab1_ingesta_validacion lab1_pronostico_pedido lab1_seguimiento; do
     docker compose exec airflow-scheduler airflow dags unpause $d; done
   ```
5. Resultados:
   - `data/forecasts/cutoff=<lunes>/forecast.csv` — pronóstico diario P10/P25/P50/P75/P90.
   - `data/forecasts/cutoff=<lunes>/orders.csv` — pedido sugerido semanal por tienda y familia.
   - `data/forecasts/cutoff=<lunes>/meta.json` — modelo, marca de agua de cada fuente, commit, hash de parámetros.
   - `reports/monitoring/monitoring_latest.csv` — seguimiento de pronósticos publicados vs. ventas reales.
   - `models/<id>/meta.json` y `backtest.csv` — comparación de modelos y decisión de reemplazo.
   - `data/quarantine/` — archivos rechazados y su motivo.

### Procesar un periodo distinto (datos nuevos o "no publicados")

La bandeja es un **contrato**: cualquier archivo nuevo con el formato
`data/inbox/<fuente>/arrival=<YYYY-MM-DD>/<fuente>_<fecha_dato>_v<N>.csv`
(fuentes: `sales`, `transactions`, `oil`, `holidays`; mismas columnas que Kaggle) es procesado por el
flujo cuando su fecha de llegada es ≤ a la fecha lógica de la ejecución. Una corrección es el mismo
`fecha_dato` con `vN` mayor. Para cambiar el periodo: ajustar `simulation.*` en `config/params.yaml`
(o lanzar un backfill: `airflow dags backfill lab1_pronostico_pedido -s <inicio> -e <fin>`).

### Reprocesar datos corregidos

Basta con que la corrección llegue a la bandeja: la ingesta diaria la valida y la incorpora; el
seguimiento recalcula las métricas con las ventas corregidas y los siguientes cortes/reentrenamientos
la usan. Para reprocesar un rango pasado: *Clear* de las tareas en la interfaz, o
`docker compose exec airflow-scheduler airflow tasks clear lab1_seguimiento -s <inicio> -e <fin> -y`. Todas las tareas son idempotentes.

## Ejecución local sin Airflow (desarrollo)

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
export PYTHONPATH=src
python -m lab1.cli bootstrap          # historia + bandeja
python -m lab1.cli weekly 2017-07-03  # ingesta + reentrenamiento si toca + pronóstico/pedido
python -m lab1.cli monitor 2017-08-20 # seguimiento
python -m lab1.cli simulate-all       # todo el periodo, día a día
python -m pytest -q
```

## Versionado con DVC (remoto en DagsHub)

Los datos, modelos y pronósticos publicados se versionan con DVC; Git solo guarda los punteros
(`*.dvc`, `dvc.lock`). Remoto: `https://dagshub.com/BrazilBatres/lab1-motor-reposicion.dvc`.

| Qué | Cómo se versiona | ¿En el remoto? |
|:--|:--|:--|
| `kaggle/*.csv` (entrada original) | dependencia de la etapa `bootstrap` en `dvc.lock` (md5 de cada archivo) | No (reglas de Kaggle) |
| `data/history`, `data/inbox` | salidas de `bootstrap` en `dvc.lock` | No: se regeneran con `dvc repro bootstrap` y DVC verifica el hash |
| `data/lake` (archivos validados) | `data/lake.dvc` | No (copia de la bandeja); se reconstruye con la ingesta |
| `data/features`, `data/quarantine` | `.dvc` | Sí |
| `models/` (modelos + backtest + decisión) | `models.dvc` | Sí |
| `data/forecasts/` (pronósticos y pedidos publicados) | `data/forecasts.dvc` | Sí |

DagsHub exige un token incluso para leer repositorios públicos. Para descargar las versiones:

```bash
pip install "dvc==3.67.1"
dvc remote modify dagshub --local auth basic
dvc remote modify dagshub --local user <usuario_dagshub>
dvc remote modify dagshub --local password <token_dagshub>   # dagshub.com > Settings > Tokens
dvc pull models.dvc data/features.dvc data/forecasts.dvc data/quarantine.dvc
dvc repro bootstrap      # con los CSV en kaggle/: regenera historia y bandeja y verifica su hash
```

El producto no depende de DVC para funcionar: con los CSV de Kaggle y Airflow se reconstruye todo
desde cero (el flujo es determinista).

## Evidencia

Toda la evidencia de esta sección proviene de la corrida completa en **Airflow 3 + Docker Compose**
(sección 4); esas mismas salidas son las versionadas en DVC.

### 1. Comparación de modelos (validación temporal, mismos cortes para todos)

Fuente: [`reports/model_comparison.csv`](reports/model_comparison.csv) (backtest del reentrenamiento del
2017-08-28; horizonte 14 días; cada fold entrena solo con objetivos anteriores a su corte).

| Corte del fold | Modelo   | WAPE  | RMSLE | Pinball | Cobertura P10–P90 | Costo de negocio |
|:---------------|:---------|------:|------:|--------:|------------------:|-----------------:|
| 2017-08-14 | baseline | 0.131 | 0.542 | 20.53 | 0.698 | 219,201 |
| 2017-08-14 | lgbm     | 0.112 | 0.413 | 17.55 | 0.779 | 187,249 |
| 2017-07-31 | baseline | 0.182 | 0.535 | 33.66 | 0.634 | 2,456,175 |
| 2017-07-31 | lgbm     | 0.166 | 0.484 | 27.47 | 0.759 | 1,987,025 |
| 2017-07-17 | baseline | 0.129 | 0.429 | 22.65 | 0.696 | 1,768,549 |
| 2017-07-17 | lgbm     | 0.127 | 0.410 | 22.03 | 0.777 | 1,647,883 |

(El fold del 2017-08-14 solo tiene 2 días observados al momento del reentrenamiento, por eso su costo es menor.)

Decisiones de reemplazo registradas (`models/<id>/meta.json`, criterio D21 de DECISIONES.md):

| Reentrenamiento | Promovido | Motivo |
|:--|:--|:--|
| lgbm_2017-06-05 | sí | mejora costo de negocio 11.0 % vs baseline (mín. 2 %), WAPE 0.162 vs 0.169 |
| lgbm_2017-07-03 | sí | mejora 10.7 %, WAPE 0.135 vs 0.136 |
| lgbm_2017-07-31 | sí | mejora 12.0 %, WAPE 0.123 vs 0.126 |
| lgbm_2017-08-28 | sí | mejora 14.0 %, WAPE 0.135 vs 0.147 |

### 2. Seguimiento del desempeño de pronósticos publicados

Fuente: [`reports/monitoring/monitoring_latest.csv`](reports/monitoring/monitoring_latest.csv)
(as-of 2017-08-28; compara cada pronóstico publicado contra las ventas reales más recientes, incluidas
correcciones). La cobertura del intervalo P10–P90 se mantiene cerca del 80 % nominal.

| Corte      | Modelo          | Días observados | WAPE  | Cobertura P10–P90 | Costo / unidad vendida |
|:-----------|:----------------|----------------:|------:|------------------:|-----------------------:|
| 2017-06-05 | lgbm_2017-06-05 | 14 | 0.133 | 0.798 | 0.144 |
| 2017-06-12 | lgbm_2017-06-05 | 14 | 0.132 | 0.798 | 0.142 |
| 2017-06-19 | lgbm_2017-06-05 | 14 | 0.120 | 0.812 | 0.129 |
| 2017-06-26 | lgbm_2017-06-05 | 14 | 0.120 | 0.817 | 0.128 |
| 2017-07-03 | lgbm_2017-07-03 | 14 | 0.117 | 0.821 | 0.123 |
| 2017-07-10 | lgbm_2017-07-03 | 14 | 0.125 | 0.805 | 0.134 |
| 2017-07-17 | lgbm_2017-07-03 | 14 | 0.127 | 0.788 | 0.142 |
| 2017-07-24 | lgbm_2017-07-03 | 14 | 0.141 | 0.784 | 0.159 |
| 2017-07-31 | lgbm_2017-07-31 | 14 | 0.166 | 0.774 | 0.168 |
| 2017-08-07 | lgbm_2017-07-31 | 9  | 0.174 | 0.769 | 0.172 |
| 2017-08-14 | lgbm_2017-07-31 | 2  | 0.121 | 0.793 | 0.126 |
| 2017-08-21 | lgbm_2017-07-31 | 0  | —     | —     | —     |
| 2017-08-28 | lgbm_2017-08-28 | 0  | —     | —     | —     |

Situaciones operativas observadas en la corrida (bandeja simulada):
- `sales_2017-07-09_v1` llegó sin la columna `sales` → **cuarentena**; el corte del 2017-07-10 se emitió
  con marca de agua de ventas 2017-07-08 (sin fallar) y la v2 reenviada se incorporó el 2017-07-12.
- `transactions_2017-06-26_v1` con valores negativos → **cuarentena**; el modelo siguió con las demás fuentes.
- 6 días de ventas recibieron corrección (v2) 4 días después; el seguimiento usa la versión corregida y
  los pronósticos ya publicados no cambian.

### 3. Reproducción de un pronóstico pasado con DVC

**a) Recuperar la versión publicada y verificar los datos de entrada** (clon limpio, salida real en
[`docs/repro_dagshub.txt`](docs/repro_dagshub.txt)):

```
$ git clone https://github.com/BrazilBatres/lab1-motor-reposicion.git && cd lab1-motor-reposicion
$ dvc pull models.dvc data/features.dvc data/forecasts.dvc data/quarantine.dvc
$ sha256sum data/forecasts/cutoff=2017-07-03/forecast.csv data/forecasts/cutoff=2017-07-03/orders.csv   # publicado por Airflow
6aa7990b685ef9ca416679de04b41732d84e3c7871f62c9b038772e4c0f9d780  data/forecasts/cutoff=2017-07-03/forecast.csv
015e1d4fb775c9c8740a3f95460d01d4e7ccd7cc9b7c13c627c136728799db05  data/forecasts/cutoff=2017-07-03/orders.csv
$ dvc repro bootstrap        # regenera data/history y data/inbox desde kaggle/
Running stage 'bootstrap':
$ dvc status bootstrap       # los hashes coinciden con los versionados en dvc.lock
Data and pipelines are up to date.
```

**b) Reconstruir el pronóstico desde cero en el mismo entorno (imagen Docker del proyecto)**: se copian
solo `config/`, `data/history` y `data/inbox` a un directorio vacío y se reemite el corte 2017-07-03
(incluido el reentrenamiento del modelo vigente). Resultado idéntico byte a byte:

```
$ docker compose exec airflow-scheduler bash
$ LAB1_ROOT=/tmp/repro python -m lab1.cli weekly 2017-06-05 > /dev/null
$ LAB1_ROOT=/tmp/repro python -m lab1.cli weekly 2017-07-03 > /dev/null
$ sha256sum data/forecasts/cutoff=2017-07-03/forecast.csv data/forecasts/cutoff=2017-07-03/orders.csv
6aa7990b685ef9ca416679de04b41732d84e3c7871f62c9b038772e4c0f9d780  data/forecasts/cutoff=2017-07-03/forecast.csv
015e1d4fb775c9c8740a3f95460d01d4e7ccd7cc9b7c13c627c136728799db05  data/forecasts/cutoff=2017-07-03/orders.csv
```

Fuera de la imagen Docker (p. ej. Windows nativo) el modelo puede diferir en decimales porque LightGBM
no es bit a bit idéntico entre plataformas; los datos sí lo son (ver DECISIONES D25).

### 4. Ejecuciones en Airflow

Corrida completa desde cero con este `docker-compose.yaml` (Airflow 3.1, LocalExecutor) en GitHub
Codespaces, siguiendo exactamente los pasos de "Levantar el producto desde cero": `lab1_bootstrap`
manual y luego *backfill* automático de 2017-06-01 a 2017-08-29, sin intervención manual sobre los datos.
Resultado: **90/90** runs de `lab1_ingesta_validacion`, **13/13** de `lab1_pronostico_pedido` (4
reentrenamientos, todos promovidos) y **89/89** de `lab1_seguimiento` (corre a las 12:00; el último día
del rango cae después de `end_date`).

| | |
|:--|:--|
| ![DAGs](docs/evidencia/01_dags.jpg) | ![Runs del pronóstico semanal](docs/evidencia/02_pronostico_pedido_runs.jpg) |
| Los 4 DAGs, último run exitoso | 13 cortes semanales; las barras altas son los reentrenamientos |
| ![Cuarentena](docs/evidencia/03_ingesta_cuarentena.jpg) | ![Reentrenamiento](docs/evidencia/04_reentrenamiento.jpg) |
| `reporte_calidad`: el archivo con transacciones negativas va a cuarentena y el run sigue en verde | `reentrenar_si_corresponde` del 2017-07-31: modelo nuevo y decisión de promoción |

Durante esta validación aparecieron (y se corrigieron, ver historial de PRs) dos problemas que solo se
ven en Airflow real: XCom de Airflow 3 rechaza `NaN` (el seguimiento ahora devuelve un resumen) y la CLI
de Airflow no agrega `dags/` al `sys.path` (la configuración común vive en `lab1.dag_config`).
