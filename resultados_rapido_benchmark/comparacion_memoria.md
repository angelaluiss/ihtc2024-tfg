# Comparación con las reglas de la competición IHTC 2024

## 1. Configuración experimental frente a las reglas oficiales

| Parámetro | IHTC 2024 (referencia) | Nuestra propuesta | ¿Dentro de las reglas? |
|---|---|---|---|
| Límite de tiempo por instancia | 600 s (máquina de referencia) | presupuesto 840 s, sin tope duro (la poda puede excederlo para garantizar factibilidad) | **NO** |
| Hilos de cómputo | 4 (referencia) | 8 (todos los núcleos) | **NO** |
| Validación de soluciones | IHTP_Validator oficial | IHTP_Validator oficial | SÍ |
| Formato de instancia y solución | JSON oficial | JSON oficial | SÍ |
| Restricciones duras (0 violaciones) | exigidas | 0 violaciones en todas | SÍ |

> **Nota metodológica:** nuestra propuesta NO compite bajo condiciones oficiales: usa más hilos y, en las instancias grandes, más tiempo del límite de referencia. Los resultados deben leerse como *cota práctica de calidad alcanzable con recursos ampliados*, no como resultado homologable en el ranking. El límite exacto de tiempo de la competición se calibra por máquina; aquí se toma 600 s como referencia.

## 2. Desviaciones respecto a las reglas

- **Hilos:** las **30/30** instancias se ejecutaron con **8 hilos** (> 4 de referencia). Afecta solo a las fases MIP (Gurobi); las metaheurísticas (greedy, SA, búsqueda local) son de un solo hilo.
- **Tiempo:** **9/28** instancias superaron el límite de 600 s. Tiempo máximo observado: **1031 s**.

| Instancia | Tiempo total (s) | Exceso sobre 600 s |
|---|---|---|
| i21 | 1030.6 | +431 |
| i17 | 1005.9 | +406 |
| i27 | 976.6 | +377 |
| i22 | 974.0 | +374 |
| i26 | 926.7 | +327 |
| i19 | 882.4 | +282 |
| i13 | 858.2 | +258 |
| i20 | 754.8 | +155 |
| i16 | 674.3 | +74 |

> El exceso de tiempo se concentra en las instancias grandes (≥150 pacientes) y proviene del bloque de Fase 2 (habitaciones + poda con readmisión), que prioriza garantizar una solución factible sobre respetar el presupuesto blando.

## 3. Resultados exactos de nuestra propuesta

| Inst | Estado | Violac. | Coste total | Unsched. | Delay | Resto | t (s) | Hilos |
|---|---|---|---|---|---|---|---|---|
| i01 | OK | 0 | 4104 | 2800 | 430 | 874 | 28.8 | 8 |
| i02 | OK | 0 | 1778 | 0 | 825 | 953 | 32.8 | 8 |
| i03 | OK | 0 | 10695 | 7600 | 1710 | 1385 | 25.5 | 8 |
| i04 | OK | 0 | 2224 | 0 | 825 | 1399 | 66.9 | 8 |
| i05 | OK | 0 | 13293 | 8250 | 3880 | 1163 | 164.2 | 8 |
| i06 | OK | 0 | 10743 | 9900 | 250 | 593 | 20.0 | 8 |
| i07 | OK | 0 | 6016 | 0 | 1310 | 4706 | 165.7 | 8 |
| i08 | OK | 0 | 11934 | 0 | 8120 | 3814 | 360.3 | 8 |
| i09 | OK | 0 | 8954 | 0 | 5535 | 3419 | 172.8 | 8 |
| i10 | OK | 0 | 26185 | 19200 | 2075 | 4910 | 548.6 | 8 |
| i11 | OK | 0 | 26050 | 24500 | 485 | 1065 | 166.1 | 8 |
| i12 | OK | 0 | 13964 | 4800 | 1200 | 7964 | 167.9 | 8 |
| i13 | OK | 0 | 20545 | 9500 | 7020 | 4025 | 858.2 | 8 |
| i14 | OK | 0 | 10661 | 3850 | 705 | 6106 | 167.9 | 8 |
| i15 | OK | 0 | 15123 | 2450 | 4730 | 7943 | 225.5 | 8 |
| i16 | OK | 0 | 14378 | 8550 | 2415 | 3413 | 674.3 | 8 |
| i17 | OK | 0 | 56540 | 28500 | 8375 | 19665 | 1005.9 | 8 |
| i18 | OK | 0 | 37998 | 33000 | 2990 | 2008 | 167.9 | 8 |
| i19 | OK | 0 | 57509 | 10000 | 27615 | 19894 | 882.4 | 8 |
| i20 | OK | 0 | 35486 | 28000 | 2880 | 4606 | 754.8 | 8 |
| i21 | OK | 0 | 30204 | 9000 | 6315 | 14889 | 1030.6 | 8 |
| i22 | OK | 0 | 76198 | 58500 | 6140 | 11558 | 974.0 | 8 |
| i23 | OK | 0 | 45618 | 9200 | 22245 | 14173 | 416.3 | 8 |
| i24 | OK | 0 | 33954 | 28350 | 1735 | 3869 | 207.2 | 8 |
| i25 | OK | 0 | 13605 | 2400 | 1955 | 9250 | 209.7 | 8 |
| i26 | OK | 0 | 77642 | 45500 | 15970 | 16172 | 926.7 | 8 |
| i27 | OK | 0 | 88674 | 61000 | 13115 | 14559 | 976.6 | 8 |
| i28 | OK | 0 | 77083 | 64350 | 1900 | 10833 | 180.8 | 8 |
| i29 | INFACTIBLE_FASE2 | – | – | – | – | – | – | 8 |
| i30 | INFACTIBLE_FASE2 | – | – | – | – | – | – | 8 |

## 4. Métricas agregadas

- **Instancias resueltas sin violaciones:** 28/30
- **Coste total acumulado:** 827.158
- **Coste medio por instancia:** 29.541
- **Coste mínimo / máximo:** 1.778 / 88.674
- **Composición del coste:** Unscheduled 58% · Delay 18% · Resto (soft) 24%
- **Tiempo total de cómputo (suma):** 193.0 min (3.22 h)
- **Instancias pendientes / infactibles:** i29, i30
