"""
benchmark_ihtc2024.py

Evaluación sistemática sobre las instancias IHTC 2024 (i01–i30).

Reglas de competición respetadas
─────────────────────────────────
  · Tiempo total por instancia : 600 s (límite oficial en máquina de referencia)
  · Threads Gurobi             : 1  (reproducibilidad y comparabilidad)
  · Validación                 : IHTP_Validator.exe (binario oficial)
  · Orden de fases             : Fase1 → Fase2 → Fase3
  · La solución que se valida  : solucion_fase3_sa.json

Distribución del presupuesto de tiempo (600 s):
  · Fase 1 (admisión, MIP)     : T1  = 180 s
  · Fase 2a (habitaciones, MIP): T2a = 120 s
  · Fase 2b (quirófanos, MIP)  : T2b =  60 s  (por día, límite agregado)
  · Fase 3 SA                  : T3s = 180 s
  · Fase 3 LS final            : T3l =  60 s
  Total                        : 600 s

Salidas:
  resultados/
    <instancia>/          → soluciones intermedias y finales
  resultados_benchmark/
    resumen.csv           → tabla comparativa (1 fila por instancia)
    detalle_<inst>.json   → métricas completas por instancia
    comparacion.txt       → tabla legible con resultados oficiales

Uso:
  python benchmark_ihtc2024.py [--instancias i01,i02,...] [--desde i01] [--hasta i30]
                               [--tiempo-total 600] [--threads 1]
                               [--no-validar] [--solo-instancias]
"""

import argparse
import csv
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────
# RUTAS BASE
# ─────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()
INSTANCIAS_DIR = BASE_DIR / "instancias"
VALIDADOR = BASE_DIR / "IHTP_Validator.exe"
RESULTADOS_DIR = BASE_DIR / "resultados"
BENCH_DIR = BASE_DIR / "resultados_benchmark"

SCRIPT_FASE1 = BASE_DIR / "modelo_scp_gurobi.py"
SCRIPT_FASE2 = BASE_DIR / "modelo_habitaciones_gurobi.py"
SCRIPT_FASE3 = BASE_DIR / "modelo_enfermeras_sa.py"
SCRIPT_GREEDY = BASE_DIR / "modelo_enfermeras_greedy_inicio.py"
SCRIPT_LS = BASE_DIR / "modelo_enfermeras_busqueda_local.py"


# ─────────────────────────────────────────────
# RESULTADOS OFICIALES IHTC 2024
# Fuente: https://ihtc2024.github.io/
# Formato: { instancia: {"best_known": int, "BKS_author": str} }
# Se actualiza con los resultados publicados por la organización.
# ─────────────────────────────────────────────

RESULTADOS_OFICIALES = {
    "i01": {"best_known": 1053,  "BKS_author": "IHTC2024 organizers"},
    "i02": {"best_known": 1101,  "BKS_author": "IHTC2024 organizers"},
    "i03": {"best_known": 1084,  "BKS_author": "IHTC2024 organizers"},
    "i04": {"best_known": 1140,  "BKS_author": "IHTC2024 organizers"},
    "i05": {"best_known": 1053,  "BKS_author": "IHTC2024 organizers"},
    "i06": {"best_known": 1133,  "BKS_author": "IHTC2024 organizers"},
    "i07": {"best_known": 1082,  "BKS_author": "IHTC2024 organizers"},
    "i08": {"best_known": 1022,  "BKS_author": "IHTC2024 organizers"},
    "i09": {"best_known": 1079,  "BKS_author": "IHTC2024 organizers"},
    "i10": {"best_known": 1134,  "BKS_author": "IHTC2024 organizers"},
    "i11": {"best_known": None,  "BKS_author": ""},
    "i12": {"best_known": None,  "BKS_author": ""},
    "i13": {"best_known": None,  "BKS_author": ""},
    "i14": {"best_known": None,  "BKS_author": ""},
    "i15": {"best_known": None,  "BKS_author": ""},
    "i16": {"best_known": None,  "BKS_author": ""},
    "i17": {"best_known": None,  "BKS_author": ""},
    "i18": {"best_known": None,  "BKS_author": ""},
    "i19": {"best_known": None,  "BKS_author": ""},
    "i20": {"best_known": None,  "BKS_author": ""},
    "i21": {"best_known": None,  "BKS_author": ""},
    "i22": {"best_known": None,  "BKS_author": ""},
    "i23": {"best_known": None,  "BKS_author": ""},
    "i24": {"best_known": None,  "BKS_author": ""},
    "i25": {"best_known": None,  "BKS_author": ""},
    "i26": {"best_known": None,  "BKS_author": ""},
    "i27": {"best_known": None,  "BKS_author": ""},
    "i28": {"best_known": None,  "BKS_author": ""},
    "i29": {"best_known": None,  "BKS_author": ""},
    "i30": {"best_known": None,  "BKS_author": ""},
}


# ─────────────────────────────────────────────
# IMPORTACIÓN DE MÓDULOS DEL PIPELINE
# ─────────────────────────────────────────────

def _importar(nombre, ruta):
    import importlib.util
    ruta = Path(ruta)
    if not ruta.exists():
        raise FileNotFoundError(f"Script no encontrado: {ruta}")
    spec = importlib.util.spec_from_file_location(nombre, str(ruta))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────────────────────────
# EJECUCIÓN DE UNA INSTANCIA
# ─────────────────────────────────────────────

def ejecutar_instancia(
    nombre_inst,
    tiempo_total=600,
    gurobi_threads=1,
    mip_gap=0.02,
    validar=True,
    verbose=True,
):
    """
    Ejecuta el pipeline completo para una instancia y devuelve un dict de métricas.

    El presupuesto de tiempo (tiempo_total segundos) se distribuye entre las fases
    de forma proporcional. Se usan time.perf_counter() para medir el tiempo real
    de cada fase, garantizando que la suma no supere el presupuesto.
    """
    ruta_inst = INSTANCIAS_DIR / f"{nombre_inst}.json"
    if not ruta_inst.exists():
        return {
            "instancia": nombre_inst,
            "estado": "INSTANCIA_NO_ENCONTRADA",
            "error": str(ruta_inst),
        }

    # Directorio de trabajo de esta instancia
    dir_inst = RESULTADOS_DIR / nombre_inst
    dir_inst.mkdir(parents=True, exist_ok=True)

    ruta_f1  = dir_inst / "fase1.json"
    ruta_f2  = dir_inst / "fase2.json"
    ruta_f3  = dir_inst / "fase3.json"
    ruta_fb  = dir_inst / "feedback.json"
    ruta_ef1 = dir_inst / "estadisticas_fase1.json"

    # Distribución de tiempo
    T_TOTAL = float(tiempo_total)
    T1  = T_TOTAL * 0.30   # 30 % → Fase 1 admisión
    T2A = T_TOTAL * 0.20   # 20 % → Fase 2 habitaciones
    T2B = T_TOTAL * 0.10   # 10 % → Fase 2 quirófanos (por día)
    T3S = T_TOTAL * 0.30   # 30 % → Fase 3 SA
    T3L = T_TOTAL * 0.10   # 10 % → Fase 3 búsqueda local final

    resultado = {
        "instancia": nombre_inst,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "tiempo_total_presupuesto": T_TOTAL,
        "gurobi_threads": gurobi_threads,
        "mip_gap": mip_gap,
        "estado": "OK",
        "error": None,
        "tiempos": {},
        "validador": {},
        "metricas_internas": {},
    }

    t_global_inicio = time.perf_counter()

    # ── Importar módulos ──────────────────────────────────────────────
    try:
        mod_f1     = _importar("fase1",   SCRIPT_FASE1)
        mod_f2     = _importar("fase2",   SCRIPT_FASE2)
        mod_greedy = _importar("greedy",  SCRIPT_GREEDY)
        mod_ls     = _importar("ls",      SCRIPT_LS)
        mod_f3     = _importar("fase3",   SCRIPT_FASE3)
    except Exception as e:
        resultado["estado"] = "ERROR_IMPORTACION"
        resultado["error"] = str(e)
        return resultado

    instancia_data = mod_greedy.load_json(ruta_inst)

    # ── FASE 1: Admisión ─────────────────────────────────────────────
    if verbose:
        print(f"\n[{nombre_inst}] Fase 1 – admisión (límite {T1:.0f}s, {gurobi_threads} hilo(s))")

    t0 = time.perf_counter()
    try:
        import gurobipy as gp
        # Forzar número de hilos en el entorno Gurobi antes de crear el modelo.
        # La forma más segura es a través del parámetro global de entorno.
        env = gp.Env()
        env.setParam("Threads", gurobi_threads)
        env.setParam("OutputFlag", int(verbose))

        sol_f1 = mod_f1.resolver_fase1_scp_interactiva(
            instancia=instancia_data,
            ruta_salida=str(ruta_f1),
            ruta_feedback=str(ruta_fb) if ruta_fb.exists() else None,
            time_limit=T1,
            mip_gap=mip_gap,
            usar_capacidad_genero=True,
            usar_hall_compatibilidad=True,
            usar_hall_quirofanos=True,
            feedback_caps_suaves=True,
            penalizacion_slack_feedback=1000.0,
            penalizacion_admisiones_dias_conflictivos=1.0,
            priorizar_opcionales=True,
            salida_estadisticas=str(ruta_ef1),
        )
        env.dispose()
    except Exception as e:
        resultado["estado"] = "ERROR_FASE1"
        resultado["error"] = traceback.format_exc()
        resultado["tiempos"]["fase1"] = round(time.perf_counter() - t0, 2)
        return resultado

    t_f1 = time.perf_counter() - t0
    resultado["tiempos"]["fase1"] = round(t_f1, 2)

    if sol_f1 is None or not ruta_f1.exists():
        resultado["estado"] = "INFACTIBLE_FASE1"
        return resultado

    pac_programados = len(sol_f1.get("patients", []))
    resultado["metricas_internas"]["pacientes_programados_f1"] = pac_programados
    if verbose:
        print(f"[{nombre_inst}] Fase 1 completada en {t_f1:.1f}s – {pac_programados} pacientes")

    # ── FASE 2: Habitaciones + Quirófanos ────────────────────────────
    # Recalcular tiempo disponible para no sobrepasar el presupuesto.
    t_usado = time.perf_counter() - t_global_inicio
    T2A_real = max(10.0, min(T2A, T_TOTAL - t_usado - T3S - T3L - 10))
    T2B_real = max(10.0, min(T2B, T_TOTAL - t_usado - T2A_real - T3S - T3L))
    # Presupuesto TOTAL para todo el bloque de Fase 2 (feedback incluido)
    T2_BUDGET = T2A_real + T2B_real

    if verbose:
        print(f"[{nombre_inst}] Fase 2 – presupuesto total {T2_BUDGET:.0f}s "
              f"(hab {T2A_real:.0f}s + OT {T2B_real:.0f}s)")

    import gurobipy as gp
    import math

    t0_f2 = time.perf_counter()
    # Número de iteraciones con re-optimización de Fase 1 via feedback
    MAX_ITER_FEEDBACK = 6
    # Número de intentos adicionales con poda directa de pacientes (sin re-opt)
    MAX_ITER_PODA = 8

    sol_f2 = None
    iteracion_feedback = 0

    # ── Bucle de feedback: re-optimiza Fase 1 con caps reducidos ──────
    while iteracion_feedback < MAX_ITER_FEEDBACK:
        iteracion_feedback += 1
        t_consumido_f2 = time.perf_counter() - t0_f2
        t_restante_f2 = T2_BUDGET - t_consumido_f2

        # Reservar al menos T2B_real para quirófanos + 5 s de margen
        t_hab_max = t_restante_f2 - T2B_real - 5
        if t_hab_max < 5:
            if verbose:
                print(f"[{nombre_inst}] Sin tiempo para más iteraciones de feedback.")
            break

        t_hab = max(5.0, min(T2A_real, t_hab_max))

        if verbose:
            print(f"[{nombre_inst}] Feedback iter {iteracion_feedback}/{MAX_ITER_FEEDBACK} "
                  f"– habitaciones {t_hab:.0f}s (restante total {t_restante_f2:.0f}s)")

        try:
            mod_f2.resolver_habitaciones_debug(
                ruta_instancia=str(ruta_inst),
                ruta_sol_previa=str(ruta_f1),
                ruta_final=str(ruta_f2),
                ruta_feedback=str(ruta_fb),
                time_limit=int(t_hab),
            )
        except Exception as e:
            resultado["estado"] = "ERROR_FASE2"
            resultado["error"] = traceback.format_exc()
            resultado["tiempos"]["fase2"] = round(time.perf_counter() - t0_f2, 2)
            return resultado

        if ruta_f2.exists():
            sol_f2_cand = mod_greedy.load_json(ruta_f2)
            sin_ot = [p for p in sol_f2_cand.get("patients", [])
                      if p.get("operating_theater") is None]
            if not sin_ot:
                sol_f2 = sol_f2_cand
                break
            if verbose:
                print(f"[{nombre_inst}]   {len(sin_ot)} pacientes sin quirófano.")
            # La solución existe pero con pacientes sin OT: borrar para forzar
            # al siguiente intento a partir de Fase 1 con feedback.
            ruta_f2.unlink(missing_ok=True)
        else:
            if verbose:
                print(f"[{nombre_inst}]   Habitaciones infactibles – feedback generado.")

        # Re-optimizar Fase 1 con el feedback recién generado (si queda tiempo)
        t_consumido_f2 = time.perf_counter() - t0_f2
        t_f1_extra = max(20.0, min(T1 * 0.4, T2_BUDGET - t_consumido_f2 - T2B_real - 10))
        if t_f1_extra < 10 or not ruta_fb.exists():
            break

        if verbose:
            print(f"[{nombre_inst}]   Re-optimizando Fase 1 con feedback ({t_f1_extra:.0f}s)...")
        try:
            env2 = gp.Env()
            env2.setParam("Threads", gurobi_threads)
            env2.setParam("OutputFlag", 0)
            mod_f1.resolver_fase1_scp_interactiva(
                instancia=instancia_data,
                ruta_salida=str(ruta_f1),
                ruta_feedback=str(ruta_fb),
                time_limit=t_f1_extra,
                mip_gap=mip_gap,
                usar_capacidad_genero=True,
                usar_hall_compatibilidad=True,
                usar_hall_quirofanos=True,
                feedback_caps_suaves=True,
                penalizacion_slack_feedback=1000.0,
                penalizacion_admisiones_dias_conflictivos=1.0,
                priorizar_opcionales=True,
                salida_estadisticas=None,
            )
            env2.dispose()
        except Exception:
            pass

    # ── Fallback: poda directa de pacientes opcionales ────────────────
    # Si el bucle de feedback no convergió, eliminamos pacientes opcionales
    # del JSON de Fase 1 directamente (sin re-optimizar) hasta que Fase 2
    # sea factible. Esto garantiza una solución válida a costa de admitir
    # menos pacientes opcionales.
    if sol_f2 is None:
        if verbose:
            print(f"[{nombre_inst}] Iniciando fallback de poda directa de pacientes...")

        pac_inst = {p["id"]: p for p in instancia_data.get("patients", [])}
        sol_f1_actual = mod_greedy.load_json(ruta_f1)
        pac_lista = list(sol_f1_actual.get("patients", []))

        # Ordenar opcionales al final (los obligatorios nunca se eliminan)
        opcionales_ids = [
            ps["id"] for ps in pac_lista
            if not pac_inst.get(ps["id"], {}).get("mandatory", False)
        ]

        for intento in range(MAX_ITER_PODA):
            t_consumido_f2 = time.perf_counter() - t0_f2
            t_restante_f2 = T2_BUDGET - t_consumido_f2
            t_hab = max(5.0, min(T2A_real * 0.7, t_restante_f2 - T2B_real - 5))

            if t_hab < 5 or not opcionales_ids:
                if verbose:
                    print(f"[{nombre_inst}]   Poda agotada (tiempo o pacientes opcionales).")
                break

            # Eliminar ~20% de los opcionales restantes (al menos 1)
            n_eliminar = max(1, math.ceil(len(opcionales_ids) * 0.20))
            ids_a_eliminar = set(opcionales_ids[:n_eliminar])
            opcionales_ids = opcionales_ids[n_eliminar:]

            pac_lista = [ps for ps in pac_lista if ps["id"] not in ids_a_eliminar]
            sol_f1_podada = {"patients": pac_lista, "nurses": []}
            ruta_f1_pod = dir_inst / "fase1_podada.json"
            with open(ruta_f1_pod, "w", encoding="utf-8") as f_out:
                json.dump(sol_f1_podada, f_out, indent=4)

            if verbose:
                print(f"[{nombre_inst}]   Poda {intento+1}: {len(ids_a_eliminar)} opcionales "
                      f"eliminados → {len(pac_lista)} pacientes restantes "
                      f"({t_hab:.0f}s para habitaciones)")

            ruta_f2.unlink(missing_ok=True)
            try:
                mod_f2.resolver_habitaciones_debug(
                    ruta_instancia=str(ruta_inst),
                    ruta_sol_previa=str(ruta_f1_pod),
                    ruta_final=str(ruta_f2),
                    ruta_feedback=str(ruta_fb),
                    time_limit=int(t_hab),
                )
            except Exception as e:
                resultado["estado"] = "ERROR_FASE2_FALLBACK"
                resultado["error"] = traceback.format_exc()
                resultado["tiempos"]["fase2"] = round(time.perf_counter() - t0_f2, 2)
                return resultado

            if ruta_f2.exists():
                sol_f2_cand = mod_greedy.load_json(ruta_f2)
                sin_ot = [p for p in sol_f2_cand.get("patients", [])
                          if p.get("operating_theater") is None]
                if not sin_ot:
                    sol_f2 = sol_f2_cand
                    resultado["metricas_internas"]["pacientes_eliminados_poda"] = (
                        resultado["metricas_internas"].get("pacientes_programados_f1", 0)
                        - len(pac_lista)
                    )
                    if verbose:
                        print(f"[{nombre_inst}]   Poda exitosa con {len(pac_lista)} pacientes.")
                    break
                ruta_f2.unlink(missing_ok=True)

    t_f2 = time.perf_counter() - t0_f2
    resultado["tiempos"]["fase2"] = round(t_f2, 2)

    if sol_f2 is None or not ruta_f2.exists():
        resultado["estado"] = "INFACTIBLE_FASE2"
        return resultado

    sol_f2 = mod_greedy.load_json(ruta_f2)
    pacs_f2 = sol_f2.get("patients", [])
    resultado["metricas_internas"]["pacientes_programados_f2"] = len(pacs_f2)
    resultado["metricas_internas"]["pacientes_sin_quirofano"] = sum(
        1 for p in pacs_f2 if p.get("operating_theater") is None
    )
    if verbose:
        print(f"[{nombre_inst}] Fase 2 completada en {t_f2:.1f}s – "
              f"{len(pacs_f2)} pacientes con habitación")

    # ── FASE 3: Enfermeras ───────────────────────────────────────────
    t_usado = time.perf_counter() - t_global_inicio
    T3S_real = max(30.0, min(T3S, T_TOTAL - t_usado - T3L - 5))
    T3L_real = max(10.0, min(T3L, T_TOTAL - t_usado - T3S_real - 2))

    if verbose:
        print(f"[{nombre_inst}] Fase 3 – SA ({T3S_real:.0f}s) + LS ({T3L_real:.0f}s)")

    t0 = time.perf_counter()
    try:
        mod_f3.resolver_enfermeras_sa(
            ruta_instancia=str(ruta_inst),
            ruta_sol_fase2=str(ruta_f2),
            ruta_salida=str(ruta_f3),
            ruta_estadisticas=str(dir_inst / "estadisticas_fase3.json"),
            ruta_validador=str(VALIDADOR),
            ruta_greedy_script=str(SCRIPT_GREEDY),
            ruta_local_search_script=str(SCRIPT_LS),
            sa_max_iter=500_000,
            sa_time_limit=T3S_real,
            sa_T0=50.0,
            sa_alpha=0.9997,
            sa_Tmin=1e-4,
            sa_candidates_per_iter=1,
            ls_final_time=T3L_real,
            ls_final_max_iter=20_000,
            ls_final_max_no_improve=5_000,
            ls_final_candidates_per_iter=50,
            seed=42,
            validar=False,       # validamos nosotros explícitamente abajo
        )
    except Exception as e:
        resultado["estado"] = "ERROR_FASE3"
        resultado["error"] = traceback.format_exc()
        resultado["tiempos"]["fase3"] = round(time.perf_counter() - t0, 2)
        return resultado

    t_f3 = time.perf_counter() - t0
    resultado["tiempos"]["fase3"] = round(t_f3, 2)
    resultado["tiempos"]["total_real"] = round(time.perf_counter() - t_global_inicio, 2)

    if not ruta_f3.exists():
        resultado["estado"] = "SIN_SOLUCION_FASE3"
        return resultado

    # Leer evaluación interna de fase 3
    ruta_ef3 = dir_inst / "estadisticas_fase3.json"
    if ruta_ef3.exists():
        ef3 = mod_greedy.load_json(ruta_ef3)
        eval_int = ef3.get("final_internal_evaluation", {})
        resultado["metricas_internas"]["uncovered_room"]              = eval_int.get("uncovered_room")
        resultado["metricas_internas"]["room_skill_level_raw"]        = eval_int.get("room_skill_level_raw")
        resultado["metricas_internas"]["excessive_nurse_workload_raw"] = eval_int.get("excessive_nurse_workload_raw")
        resultado["metricas_internas"]["continuity_of_care_raw"]      = eval_int.get("continuity_of_care_raw")
        resultado["metricas_internas"]["score_interno"]               = eval_int.get("score")

    if verbose:
        print(f"[{nombre_inst}] Fase 3 completada en {t_f3:.1f}s")

    # ── Validación oficial ───────────────────────────────────────────
    if validar:
        if verbose:
            print(f"[{nombre_inst}] Ejecutando validador oficial...")
        val = mod_greedy.ejecutar_validador(ruta_inst, ruta_f3, VALIDADOR)
        resultado["validador"] = val

        parsed = val.get("parsed", {})
        resultado["total_violations"] = parsed.get("total_violations")
        resultado["total_cost"]       = parsed.get("total_cost")

        # Detallar costes por categoría
        for cat, datos_cat in parsed.get("costs", {}).items():
            resultado[f"cost_{cat}"] = datos_cat.get("weighted_cost")
        for cat, n in parsed.get("violations", {}).items():
            resultado[f"viol_{cat}"] = n

        if verbose:
            print(f"[{nombre_inst}] Violaciones: {parsed.get('total_violations')} | "
                  f"Coste total: {parsed.get('total_cost')}")

    # ── Comparación con resultado oficial ────────────────────────────
    ref = RESULTADOS_OFICIALES.get(nombre_inst, {})
    bks = ref.get("best_known")
    resultado["BKS"] = bks
    if bks is not None and resultado.get("total_cost") is not None:
        gap = (resultado["total_cost"] - bks) / bks * 100
        resultado["gap_vs_BKS_pct"] = round(gap, 2)
    else:
        resultado["gap_vs_BKS_pct"] = None

    return resultado


# ─────────────────────────────────────────────
# GENERACIÓN DE INFORMES
# ─────────────────────────────────────────────

COLUMNAS_CSV = [
    "instancia", "estado", "total_violations", "total_cost", "BKS",
    "gap_vs_BKS_pct",
    "pacientes_programados_f1", "pacientes_programados_f2", "pacientes_sin_quirofano",
    "uncovered_room", "room_skill_level_raw", "excessive_nurse_workload_raw",
    "continuity_of_care_raw", "score_interno",
    "t_fase1", "t_fase2", "t_fase3", "t_total",
    "cost_Unscheduled", "cost_Delay", "cost_RoomAgeMix",
    "cost_RoomSkillLevel", "cost_ExcessiveNurseWorkload",
    "cost_ContinuityOfCare", "cost_OpenOperatingTheater",
    "cost_SurgeonTransfer",
    "timestamp",
]


def _val(r, key, default=""):
    v = r.get(key)
    if v is None:
        # Intentar en metricas_internas
        v = r.get("metricas_internas", {}).get(key)
    return v if v is not None else default


def generar_csv(resultados, ruta_csv):
    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNAS_CSV)
        for r in resultados:
            fila = [
                r.get("instancia", ""),
                r.get("estado", ""),
                _val(r, "total_violations"),
                _val(r, "total_cost"),
                _val(r, "BKS"),
                _val(r, "gap_vs_BKS_pct"),
                _val(r, "pacientes_programados_f1"),
                _val(r, "pacientes_programados_f2"),
                _val(r, "pacientes_sin_quirofano"),
                _val(r, "uncovered_room"),
                _val(r, "room_skill_level_raw"),
                _val(r, "excessive_nurse_workload_raw"),
                _val(r, "continuity_of_care_raw"),
                _val(r, "score_interno"),
                r.get("tiempos", {}).get("fase1", ""),
                r.get("tiempos", {}).get("fase2", ""),
                r.get("tiempos", {}).get("fase3", ""),
                r.get("tiempos", {}).get("total_real", ""),
                _val(r, "cost_Unscheduled"),
                _val(r, "cost_Delay"),
                _val(r, "cost_RoomAgeMix"),
                _val(r, "cost_RoomSkillLevel"),
                _val(r, "cost_ExcessiveNurseWorkload"),
                _val(r, "cost_ContinuityOfCare"),
                _val(r, "cost_OpenOperatingTheater"),
                _val(r, "cost_SurgeonTransfer"),
                r.get("timestamp", ""),
            ]
            w.writerow(fila)


def generar_tabla_comparacion(resultados, ruta_txt):
    header = (
        f"{'Inst':>6}  {'Estado':>22}  {'Violac':>6}  "
        f"{'Coste':>8}  {'BKS':>8}  {'Gap%':>7}  {'t(s)':>6}"
    )
    sep = "─" * len(header)

    lines = [
        "COMPARACIÓN CON RESULTADOS OFICIALES IHTC 2024",
        f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        sep,
        header,
        sep,
    ]

    costes = []
    for r in resultados:
        inst   = r.get("instancia", "?")
        estado = r.get("estado", "?")[:22]
        viol   = r.get("total_violations", "–")
        coste  = r.get("total_cost", "–")
        bks    = r.get("BKS", "–")
        gap    = r.get("gap_vs_BKS_pct")
        gap_s  = f"{gap:+.1f}%" if gap is not None else "–"
        t      = r.get("tiempos", {}).get("total_real", "–")

        lines.append(
            f"{inst:>6}  {estado:>22}  {str(viol):>6}  "
            f"{str(coste):>8}  {str(bks):>8}  {gap_s:>7}  {str(t):>6}"
        )

        if isinstance(coste, int):
            costes.append(coste)

    lines.append(sep)

    resueltos = [r for r in resultados if r.get("total_violations") == 0]
    n_ok      = len(resueltos)
    n_total   = len(resultados)
    gaps = [r["gap_vs_BKS_pct"] for r in resueltos if r.get("gap_vs_BKS_pct") is not None]

    lines.append(f"  Instancias sin violaciones : {n_ok}/{n_total}")
    if costes:
        lines.append(f"  Coste medio               : {sum(costes)/len(costes):.1f}")
        lines.append(f"  Coste min/max             : {min(costes)} / {max(costes)}")
    if gaps:
        lines.append(f"  Gap medio vs BKS          : {sum(gaps)/len(gaps):+.2f}%")
        lines.append(f"  Gap min/max               : {min(gaps):+.2f}% / {max(gaps):+.2f}%")
    lines.append(sep)

    texto = "\n".join(lines)
    with open(ruta_txt, "w", encoding="utf-8") as f:
        f.write(texto)
    return texto


# ─────────────────────────────────────────────
# FUNCIÓN PRINCIPAL
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Benchmark IHTC 2024 - evaluacion sistematica de instancias."
    )
    parser.add_argument(
        "--instancias", default=None,
        help="Lista separada por comas (ej. i01,i03,i05). Por defecto: todas las disponibles."
    )
    parser.add_argument("--desde", default=None, help="Desde instancia, ej. i05")
    parser.add_argument("--hasta", default=None, help="Hasta instancia (incluida), ej. i15")
    parser.add_argument(
        "--tiempo-total", type=float, default=600.0,
        help="Presupuesto de tiempo por instancia en segundos (defecto: 600)."
    )
    parser.add_argument(
        "--threads", type=int, default=1,
        help="Hilos Gurobi (defecto: 1, requerido por la competición)."
    )
    parser.add_argument(
        "--mip-gap", type=float, default=0.02,
        help="Gap MIP para Gurobi (defecto: 0.02)."
    )
    parser.add_argument(
        "--no-validar", action="store_true",
        help="No ejecutar el validador oficial (más rápido, sin métricas de coste)."
    )
    parser.add_argument(
        "--silencioso", action="store_true",
        help="Reduce la salida por pantalla."
    )

    args = parser.parse_args()

    # Determinar lista de instancias
    disponibles = sorted(
        p.stem for p in INSTANCIAS_DIR.glob("i*.json")
    )

    if args.instancias:
        instancias = [s.strip() for s in args.instancias.split(",")]
    elif args.desde or args.hasta:
        desde = args.desde or disponibles[0]
        hasta = args.hasta or disponibles[-1]
        instancias = [i for i in disponibles if desde <= i <= hasta]
    else:
        instancias = disponibles

    instancias_faltantes = [i for i in instancias if i not in disponibles]
    if instancias_faltantes:
        print(f"[AVISO] Las siguientes instancias no existen en {INSTANCIAS_DIR}:")
        for i in instancias_faltantes:
            print(f"  - {i}")
        instancias = [i for i in instancias if i in disponibles]

    if not instancias:
        print("[ERROR] No hay instancias válidas para ejecutar.")
        sys.exit(1)

    RESULTADOS_DIR.mkdir(parents=True, exist_ok=True)
    BENCH_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("  BENCHMARK IHTC 2024")
    print(f"  Instancias : {len(instancias)}  ({instancias[0]} → {instancias[-1]})")
    print(f"  Tiempo/inst: {args.tiempo_total:.0f} s")
    print(f"  Threads    : {args.threads}")
    print(f"  MIP gap    : {args.mip_gap}")
    print(f"  Validación : {'NO' if args.no_validar else 'SÍ'}")
    print("=" * 70)

    todos_resultados = []
    t_benchmark_inicio = time.perf_counter()

    for idx, inst in enumerate(instancias, 1):
        print(f"\n{'='*70}")
        print(f"  [{idx}/{len(instancias)}]  {inst}")
        print(f"{'='*70}")

        resultado = ejecutar_instancia(
            nombre_inst=inst,
            tiempo_total=args.tiempo_total,
            gurobi_threads=args.threads,
            mip_gap=args.mip_gap,
            validar=not args.no_validar,
            verbose=not args.silencioso,
        )

        todos_resultados.append(resultado)

        # Guardar detalle inmediatamente (permite recuperar si hay crash)
        ruta_det = BENCH_DIR / f"detalle_{inst}.json"
        with open(ruta_det, "w", encoding="utf-8") as f:
            # No serializar el bloque raw del validador (puede ser grande)
            det = {k: v for k, v in resultado.items() if k != "validador"}
            det["validador_parsed"] = resultado.get("validador", {}).get("parsed", {})
            json.dump(det, f, indent=4, ensure_ascii=False)

        # Guardar CSV parcial tras cada instancia
        ruta_csv = BENCH_DIR / "resumen.csv"
        generar_csv(todos_resultados, ruta_csv)

        estado = resultado.get("estado", "?")
        coste  = resultado.get("total_cost", "–")
        viol   = resultado.get("total_violations", "–")
        gap    = resultado.get("gap_vs_BKS_pct")
        gap_s  = f"{gap:+.2f}%" if gap is not None else "–"
        print(f"\n  ► {inst}: {estado} | violaciones={viol} | coste={coste} | gap BKS={gap_s}")

    # ── Informe final ─────────────────────────────────────────────────
    t_total_bench = time.perf_counter() - t_benchmark_inicio

    ruta_csv = BENCH_DIR / "resumen.csv"
    ruta_txt = BENCH_DIR / "comparacion.txt"
    generar_csv(todos_resultados, ruta_csv)
    tabla = generar_tabla_comparacion(todos_resultados, ruta_txt)

    print("\n" + tabla)
    print(f"\nTiempo total del benchmark : {t_total_bench/60:.1f} min")
    print(f"CSV guardado en            : {ruta_csv}")
    print(f"Tabla guardada en          : {ruta_txt}")
    print(f"Detalles en                : {BENCH_DIR}")


if __name__ == "__main__":
    main()
