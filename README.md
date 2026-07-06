# Planificación integrada hospitalaria mediante descomposición y un cuadro de mandos para su diagnóstico (IHTC 2024)

Trabajo de Fin de Grado sobre el problema de planificación integrada hospitalaria
propuesto en la *Integrated Healthcare Timetabling Competition* 2024 (IHTC 2024).
El problema combina, en un mismo horizonte, la **admisión de pacientes**, la
**asignación de habitaciones**, la **planificación quirúrgica** y la **asignación de
enfermería**.

La propuesta resuelve el problema por **descomposición secuencial con realimentación
y readmisión**: las fases de admisión, habitaciones y quirófanos se modelan con
**programación entera mixta** (Gurobi) y la de enfermería con una **construcción
voraz + *Simulated Annealing* + búsqueda local**. Las soluciones se validan con el
validador oficial y se analizan con un **cuadro de mandos** interactivo.

---

## Datos de terceros (no incluidos en el repositorio)

Por tamaño y por ser material de la competición, **las instancias, el validador y las
soluciones oficiales no se versionan aquí**: se descargan de la web oficial del
IHTC 2024 y se colocan en las carpetas indicadas.

Página oficial de la competición: <https://ihtc2024.github.io/>

| Recurso | De dónde se obtiene | Dónde colocarlo |
|---|---|---|
| **Instancias** `i01.json … i30.json` | Sección *Instances* de la web oficial | `instancias/` |
| **Validador** `IHTP_Validator` (código C++ y/o binario) | Sección *Validator* de la web oficial | raíz del repo (p. ej. `IHTP_Validator.exe` en Windows) |
| **Soluciones oficiales** (mejor resultado publicado por instancia) | Sección *Results* de la web oficial | `soluciones_oficiales/` (como `sol_1.json … sol_30.json`) |

Una vez colocados estos datos, el resto del proyecto (código y resultados) es
autosuficiente y reproducible.

---

## Estructura del repositorio

```
.
├── lector_datos.py                     # carga y normalización de instancias
├── modelo_scp_gurobi.py                # Fase 1: admisión (MILP)
├── modelo_habitaciones_gurobi.py       # Fase 2: habitaciones / quirófanos (MILP)
├── modelo_enfermeras_greedy_inicio.py  # Fase 3: construcción voraz
├── modelo_enfermeras_sa.py             # Fase 3: Simulated Annealing
├── modelo_enfermeras_busqueda_local.py # Fase 3: búsqueda local
├── ejecutar_maximo.py                  # pipeline completo (condiciones ampliadas)
├── ejecutar_rapido.py                  # pipeline con presupuesto de tiempo reducido
├── ejecutar_competicion.py             # pipeline bajo reglas oficiales (4 hilos, 600 s)
├── benchmark_ihtc2024.py               # evaluación sistemática i01–i30
├── procesar_soluciones_oficiales.py    # valida las soluciones oficiales y genera su desglose
├── generar_dashboard.py                # genera el cuadro de mandos (dashboard.html)
├── figura_*.py / figuras_*.py          # figuras de la memoria
├── comparacion_*.py                    # informes de comparación (CSV/Word)
│
├── resultados_rapido_benchmark/        # RESULTADOS procesados (condiciones ampliadas)
├── resultados_competicion_benchmark/   # RESULTADOS procesados (reglas de competición)
├── resultados_oficiales_benchmark/     # desglose de las soluciones oficiales (referencia)
├── resultados_benchmark/               # resultados agregados y comparaciones
├── diagnostico_admisiones/*.csv        # CSV fuente del análisis de admisiones
├── diagnostico_cirujanos_opcionales/*.csv  # CSV fuente del análisis de quirófanos
│
├── figuras/                            # figuras de la memoria (PNG)
├── dashboard.html                      # cuadro de mandos (condiciones ampliadas)
├── dashboard_competicion.html          # cuadro de mandos (reglas de competición)
├── requirements.txt
└── README.md
```

### Qué se versiona y qué no

- **Sí se versiona:** el **código fuente** completo, los **resultados procesados**
  (desglose de coste y violaciones por instancia), los **CSV** de los que se extrae la
  información que se transforma en tablas y figuras, las **figuras** y el **cuadro de
  mandos**.
- **No se versiona** (se referencia o se regenera): las **instancias** de entrada, el
  **validador** y las **soluciones oficiales** (datos de terceros, ver arriba), y las
  **salidas intermedias** por fase (`resultados/`, `resultados_rapido/`,
  `resultados_competicion/`, …), que se regeneran ejecutando los scripts.

---

## Requisitos

- **Python 3** con las dependencias de `requirements.txt`:
  ```bash
  pip install -r requirements.txt
  ```
- **Gurobi** con licencia válida (`gurobipy`) para las fases de programación entera.
- El **validador oficial** del IHTC 2024 (ver la tabla de datos de terceros).

## Reproducir los resultados

1. Descargar y colocar los datos de terceros (instancias, validador y, opcionalmente,
   soluciones oficiales) según la tabla anterior.
2. Ejecutar el pipeline sobre una o varias instancias, por ejemplo:
   ```bash
   python ejecutar_competicion.py        # bajo reglas oficiales (4 hilos, 600 s)
   python ejecutar_rapido.py             # presupuesto de tiempo reducido
   ```
3. Validar y generar el desglose de coste con el validador oficial
   (`benchmark_ihtc2024.py` / `procesar_soluciones_oficiales.py`).
4. Regenerar figuras y cuadro de mandos:
   ```bash
   python generar_dashboard.py
   ```

## Cuadro de mandos

`dashboard.html` y `dashboard_competicion.html` son autónomos: se abren directamente en
el navegador y permiten explorar las métricas por instancia, la estructura de cada
solución y la comparación con el mejor resultado de la competición.
