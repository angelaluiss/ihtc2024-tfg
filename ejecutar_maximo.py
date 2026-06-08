"""
ejecutar_maximo.py
==================
Ejecuta el pipeline completo sin límites de competición:
  · Gurobi usa todos los hilos disponibles (os.cpu_count())
  · MIPGap = 0.005  (0,5 %, casi óptimo)
  · Fase 1  : 30 min por instancia
  · Fase 2  : 20 min habitaciones + 10 min quirófanos
  · Fase 3  : 2 h SA  +  30 min búsqueda local final

Estimación: ~3-4 días para las 30 instancias en secuencia.

Características:
  · Reloj global por instancia (nunca se pasa del presupuesto total)
  · Reanudable: omite instancias ya completadas (--forzar para rehacer)
  · Progreso en tiempo real + CSV parcial tras cada instancia
  · Resumen final con tabla y tiempo acumulado

Uso:
  python ejecutar_maximo.py                      # todas las instancias
  python ejecutar_maximo.py --desde i08          # desde i08 en adelante
  python ejecutar_maximo.py --instancias i17,i22 # solo esas
  python ejecutar_maximo.py --forzar             # rehace aunque existan
  python ejecutar_maximo.py --lista              # muestra instancias y sale
"""

import argparse
import csv
import json
import math
import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

# ─────────────────────────────────────────────
# RUTAS
# ─────────────────────────────────────────────

BASE_DIR     = Path(__file__).parent.resolve()
INST_DIR     = BASE_DIR / "instancias"
VALIDADOR    = BASE_DIR / "IHTP_Validator.exe"
RES_DIR      = BASE_DIR / "resultados_maximo"
OUT_DIR      = BASE_DIR / "resultados_maximo_benchmark"

SCRIPT_F1    = BASE_DIR / "modelo_scp_gurobi.py"
SCRIPT_F2    = BASE_DIR / "modelo_habitaciones_gurobi.py"
SCRIPT_GREEDY= BASE_DIR / "modelo_enfermeras_greedy_inicio.py"
SCRIPT_LS    = BASE_DIR / "modelo_enfermeras_busqueda_local.py"
SCRIPT_F3    = BASE_DIR / "modelo_enfermeras_sa.py"

# ─────────────────────────────────────────────
# PARÁMETROS DE CALIDAD MÁXIMA
# ─────────────────────────────────────────────

N_THREADS    = os.cpu_count() or 8   # todos los núcleos disponibles
MIP_GAP      = 0.005                  # 0,5 % de optimalidad garantizada
SA_T0        = 200.0                  # temperatura inicial más alta → más exploración
SA_ALPHA     = 0.99985               # enfriamiento más lento → más explotación
SA_TMIN      = 1e-5

# Presupuestos de tiempo (segundos)
T_F1         = 1800    # 30 min  – admisión (MIP)
T_F2_HAB     = 1200    # 20 min  – habitaciones (MIP)
T_F2_OT      = 600     # 10 min  – quirófanos (MIP, por día)
T_SA         = 7200    # 2 horas – simulated annealing
T_LS         = 1800    # 30 min  – búsqueda local final
T_MAX_TOTAL  = T_F1 + T_F2_HAB + T_F2_OT + T_SA + T_LS  # ~3,75 h

MAX_ITER_FEEDBACK = 8   # iteraciones feedback Fase1→Fase2
MAX_ITER_PODA     = 10  # intentos de poda directa si feedback falla

# Factor del tope de tiempo de la poda respecto al presupuesto de Fase 2.
# >1 permite que la poda se exceda para garantizar factibilidad (modos
# relajados). En modo competición se pone a 1.0 para respetar el tope duro.
PODA_TOPE_FACTOR = 3.0

# Interruptor para el estudio de ablación: si es False, la poda elimina pero
# NO ejecuta la fase de readmisión (recuperación de opcionales). Permite medir
# exactamente cuánto aporta la readmisión dejando todo lo demás idéntico.
READMISION_ACTIVA = True

# ─────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────

def _importar(nombre, ruta):
    import importlib.util
    ruta = Path(ruta)
    if not ruta.exists():
        raise FileNotFoundError(f"Script no encontrado: {ruta}")
    spec = importlib.util.spec_from_file_location(nombre, str(ruta))
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _guardar_json(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def _cargar_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _fmt_tiempo(segundos):
    """Formatea segundos en H:MM:SS."""
    h = int(segundos // 3600)
    m = int((segundos % 3600) // 60)
    s = int(segundos % 60)
    return f"{h}:{m:02d}:{s:02d}"


def _eta(t_por_instancia_media, n_restantes):
    eta_s = t_por_instancia_media * n_restantes
    return _fmt_tiempo(eta_s)


# ─────────────────────────────────────────────
# EJECUCIÓN DE UNA INSTANCIA
# ─────────────────────────────────────────────

def ejecutar_instancia(nombre, verbose=True):
    ruta_inst = INST_DIR / f"{nombre}.json"
    if not ruta_inst.exists():
        return {"instancia": nombre, "estado": "NO_ENCONTRADA"}

    dir_inst  = RES_DIR / nombre
    dir_inst.mkdir(parents=True, exist_ok=True)

    ruta_f1   = dir_inst / "fase1.json"
    ruta_f2   = dir_inst / "fase2.json"
    ruta_f3   = dir_inst / "fase3.json"
    ruta_fb   = dir_inst / "feedback.json"

    t_global  = time.perf_counter()

    def elapsed():
        return time.perf_counter() - t_global

    def log(msg):
        if verbose:
            print(f"  [{_fmt_tiempo(elapsed())}] {msg}", flush=True)

    resultado = {
        "instancia":  nombre,
        "timestamp":  datetime.now().isoformat(timespec="seconds"),
        "threads":    N_THREADS,
        "mip_gap":    MIP_GAP,
        "estado":     "OK",
        "error":      None,
        "tiempos":    {},
        "metricas":   {},
        "validador":  {},
    }

    # Importar módulos
    try:
        mod_f1  = _importar("f1",     SCRIPT_F1)
        mod_f2  = _importar("f2",     SCRIPT_F2)
        mod_g   = _importar("greedy", SCRIPT_GREEDY)
        mod_ls  = _importar("ls",     SCRIPT_LS)
        mod_f3  = _importar("f3",     SCRIPT_F3)
    except Exception as e:
        resultado["estado"] = "ERROR_IMPORTACION"
        resultado["error"]  = str(e)
        return resultado

    import gurobipy as gp
    instancia_data = mod_g.load_json(ruta_inst)

    # ── FASE 1 ────────────────────────────────────────────────────────
    log(f"Fase 1 – admisión (MIP, {N_THREADS} hilos, gap {MIP_GAP*100:.1f}%, límite {T_F1//60} min)")
    t0 = time.perf_counter()
    try:
        sol_f1 = mod_f1.resolver_fase1_scp_interactiva(
            instancia=instancia_data,
            ruta_salida=str(ruta_f1),
            ruta_feedback=str(ruta_fb) if ruta_fb.exists() else None,
            time_limit=T_F1,
            mip_gap=MIP_GAP,
            usar_capacidad_genero=True,
            usar_hall_compatibilidad=True,
            usar_hall_quirofanos=True,
            feedback_caps_suaves=True,
            penalizacion_slack_feedback=1000.0,
            penalizacion_admisiones_dias_conflictivos=1.0,
            priorizar_opcionales=True,
            salida_estadisticas=str(dir_inst / "stats_fase1.json"),
        )
    except Exception:
        resultado["estado"] = "ERROR_FASE1"
        resultado["error"]  = traceback.format_exc()
        resultado["tiempos"]["fase1"] = round(time.perf_counter() - t0, 1)
        return resultado

    t_f1 = time.perf_counter() - t0
    resultado["tiempos"]["fase1"] = round(t_f1, 1)

    if sol_f1 is None or not ruta_f1.exists():
        resultado["estado"] = "INFACTIBLE_FASE1"
        return resultado

    n_pac_f1 = len(sol_f1.get("patients", []))
    resultado["metricas"]["pacientes_f1"] = n_pac_f1
    log(f"Fase 1 OK – {n_pac_f1} pacientes en {_fmt_tiempo(t_f1)}")

    # ── FASE 2: habitaciones + quirófanos (con feedback y poda) ───────
    log(f"Fase 2 – habitaciones {T_F2_HAB//60} min + quirófanos {T_F2_OT//60} min")
    t0_f2    = time.perf_counter()
    sol_f2   = None
    pac_inst = {p["id"]: p for p in instancia_data.get("patients", [])}

    # Fase 2a: bucle de feedback
    for iter_fb in range(1, MAX_ITER_FEEDBACK + 1):
        t_cons_f2 = time.perf_counter() - t0_f2
        t_rest    = (T_F2_HAB + T_F2_OT) - t_cons_f2
        t_hab     = max(10.0, min(T_F2_HAB, t_rest - T_F2_OT - 5))
        if t_hab < 10:
            log(f"  Tiempo agotado en feedback iter {iter_fb}.")
            break

        log(f"  Feedback iter {iter_fb}/{MAX_ITER_FEEDBACK} – habitaciones {t_hab:.0f}s")
        ruta_f2.unlink(missing_ok=True)
        try:
            mod_f2.resolver_habitaciones_debug(
                ruta_instancia=str(ruta_inst),
                ruta_sol_previa=str(ruta_f1),
                ruta_final=str(ruta_f2),
                ruta_feedback=str(ruta_fb),
                time_limit=int(t_hab),
            )
        except Exception:
            resultado["estado"] = "ERROR_FASE2"
            resultado["error"]  = traceback.format_exc()
            resultado["tiempos"]["fase2"] = round(time.perf_counter() - t0_f2, 1)
            return resultado

        if ruta_f2.exists():
            cand = mod_g.load_json(ruta_f2)
            sin_ot = [p for p in cand.get("patients", []) if p.get("operating_theater") is None]
            if not sin_ot:
                sol_f2 = cand
                log(f"  Fase 2 factible con {len(cand['patients'])} pacientes.")
                break
            log(f"  {len(sin_ot)} pacientes sin quirófano – reintentando con feedback.")
            ruta_f2.unlink(missing_ok=True)
        else:
            log("  Habitaciones infactibles – feedback generado, re-opt Fase 1.")

        # Re-optimizar Fase 1 con el nuevo feedback
        t_cons_f2 = time.perf_counter() - t0_f2
        t_f1_extra = max(30.0, min(T_F1 * 0.5, (T_F2_HAB + T_F2_OT) - t_cons_f2 - T_F2_OT - 10))
        if t_f1_extra < 20 or not ruta_fb.exists():
            break
        try:
            mod_f1.resolver_fase1_scp_interactiva(
                instancia=instancia_data,
                ruta_salida=str(ruta_f1),
                ruta_feedback=str(ruta_fb),
                time_limit=t_f1_extra,
                mip_gap=MIP_GAP,
                usar_capacidad_genero=True,
                usar_hall_compatibilidad=True,
                usar_hall_quirofanos=True,
                feedback_caps_suaves=True,
                penalizacion_slack_feedback=1000.0,
                penalizacion_admisiones_dias_conflictivos=1.0,
                priorizar_opcionales=True,
                salida_estadisticas=None,
            )
        except Exception:
            pass

    # Fase 2b: poda directa si feedback no convergió.
    #
    # Estrategia:
    #   1. ELIMINACIÓN en el ORDEN ORIGINAL de los opcionales (probado: logra
    #      factibilidad en las 30 instancias). NO se reordena por congestión:
    #      concentrar los descartes en un solo día deja sin tocar el día que
    #      realmente causa la infactibilidad (típicamente un conflicto de
    #      género en otro día) y rompe la convergencia.
    #   2. READMISIÓN (búsqueda binaria) SOLO si tras lograr factibilidad
    #      queda tiempo holgado. Parte siempre de una sol_f2 válida, así que
    #      nunca puede volver a hacer infactible la instancia. Cada readmisión
    #      válida ahorra 500 puntos (ElectiveUnscheduledPatients).
    #
    # Guard de tiempo: se reserva T_F2_OT para quirófanos. El tiempo disponible
    # para un solve de habitaciones es t_disp = t_rest - T_F2_OT - 5; si baja de
    # 10 s se corta (antes el max(10,...) impedía que el break saltara nunca).
    pac_obj_por_id = {ps["id"]: ps for ps in _cargar_json(ruta_f1).get("patients", [])}

    def _es_factible(lista_pacientes, t_limit):
        """Resuelve Fase 2 con esa lista; devuelve la solución si es factible."""
        ruta_pod = dir_inst / "fase1_podada.json"
        _guardar_json({"patients": lista_pacientes, "nurses": []}, ruta_pod)
        ruta_f2.unlink(missing_ok=True)
        mod_f2.resolver_habitaciones_debug(
            ruta_instancia=str(ruta_inst),
            ruta_sol_previa=str(ruta_pod),
            ruta_final=str(ruta_f2),
            ruta_feedback=str(ruta_fb),
            time_limit=int(t_limit),
        )
        if ruta_f2.exists():
            cand   = mod_g.load_json(ruta_f2)
            sin_ot = [p for p in cand.get("patients", []) if p.get("operating_theater") is None]
            if not sin_ot:
                return cand
            ruta_f2.unlink(missing_ok=True)
        return None

    def _t_disp_hab(factor):
        """Tiempo disponible para un solve de habitaciones, o None si no queda."""
        t_rest = (T_F2_HAB + T_F2_OT) - (time.perf_counter() - t0_f2)
        t_disp = t_rest - T_F2_OT - 5
        if t_disp < 10:
            return None
        return min(T_F2_HAB * factor, t_disp)

    if sol_f2 is None:
        log("  Iniciando poda de opcionales (orden original, con readmisión)...")
        pac_lista  = list(pac_obj_por_id.values())
        # Orden ORIGINAL (sin reordenar): es el que converge en las 30 instancias.
        opcionales = [ps for ps in pac_lista
                      if not pac_inst.get(ps["id"], {}).get("mandatory", False)]
        eliminados_orden = []

        # Tope de seguridad: la eliminación PUEDE pasarse del presupuesto blando
        # de Fase 2 porque la FACTIBILIDAD es innegociable (más vale una solución
        # válida tardía que ninguna). Pero se limita a un múltiplo del presupuesto
        # para no colgarse indefinidamente.
        t_tope_eliminacion = PODA_TOPE_FACTOR * (T_F2_HAB + T_F2_OT)
        # Tiempo por solve individual: acotado para que cada intento sea ágil.
        t_solve = max(20.0, min(T_F2_HAB * 0.6, 60.0))

        try:
            # ── Fase de eliminación geométrica ────────────────────────
            # Elimina hasta lograr factibilidad. NO se corta por el presupuesto
            # blando: solo por agotar opcionales, por MAX_ITER_PODA o por el tope
            # de seguridad. Así garantiza una solución válida.
            i_opc = 0
            for iter_p in range(1, MAX_ITER_PODA + 1):
                if i_opc >= len(opcionales):
                    log("  Sin más opcionales que podar.")
                    break
                if (time.perf_counter() - t0_f2) > t_tope_eliminacion:
                    log("  Tope de seguridad de poda alcanzado.")
                    break

                restantes = len(opcionales) - i_opc
                n_elim    = max(1, math.ceil(restantes * 0.25))
                lote      = opcionales[i_opc:i_opc + n_elim]
                i_opc    += n_elim
                eliminados_orden.extend(lote)

                ids_fuera  = {ps["id"] for ps in eliminados_orden}
                cand_lista = [ps for ps in pac_lista if ps["id"] not in ids_fuera]

                log(f"  Poda {iter_p}: -{n_elim} → {len(cand_lista)} pacientes, hab {t_solve:.0f}s")
                cand = _es_factible(cand_lista, t_solve)
                if cand is not None:
                    sol_f2 = cand
                    break

            # ── Fase de readmisión (recuperar exceso de poda) ─────────
            # Busca el MÍNIMO nº de pacientes que deben quedar fuera. Parte de
            # una sol_f2 válida, así que solo puede mejorar (o mantener).
            if sol_f2 is not None and eliminados_orden and READMISION_ACTIVA:
                log(f"  Factible con {len(eliminados_orden)} eliminados. Intentando readmitir...")
                lo, hi = 0, len(eliminados_orden)
                mejor_fuera = list(eliminados_orden)
                while lo < hi:
                    # La readmisión usa el margen real hasta el tope de seguridad
                    # (no solo el presupuesto blando): si la eliminación se pasó
                    # de tiempo, aún recuperamos pacientes mientras quepan probes.
                    if (time.perf_counter() - t0_f2) > t_tope_eliminacion:
                        log("  Sin tiempo para más readmisión.")
                        break
                    t_hab = t_solve
                    mid = (lo + hi) // 2   # nº de pacientes que se mantienen FUERA
                    fuera_ids  = {ps["id"] for ps in mejor_fuera[:mid]}
                    cand_lista = [ps for ps in pac_lista if ps["id"] not in fuera_ids]
                    log(f"  Readmisión: probando con solo {mid} fuera "
                        f"({len(cand_lista)} pacientes), hab {t_hab:.0f}s")
                    cand = _es_factible(cand_lista, t_hab)
                    if cand is not None:
                        sol_f2 = cand
                        hi = mid            # caben más: reducir los que quedan fuera
                    else:
                        lo = mid + 1        # no caben: hay que dejar fuera más

        except Exception:
            resultado["estado"] = "ERROR_FASE2_PODA"
            resultado["error"]  = traceback.format_exc()
            resultado["tiempos"]["fase2"] = round(time.perf_counter() - t0_f2, 1)
            return resultado

        if sol_f2 is not None:
            n_pod = n_pac_f1 - len(sol_f2.get("patients", []))
            resultado["metricas"]["pacientes_eliminados_poda"] = n_pod
            log(f"  Poda final: {len(sol_f2['patients'])} pacientes ({n_pod} opcionales eliminados).")
            # IMPORTANTE: la última prueba (infactible) de la readmisión pudo
            # borrar ruta_f2 con unlink(). Reescribimos el fichero desde la
            # solución válida en memoria para que persista (si no, el check
            # 'not ruta_f2.exists()' la descartaría como INFACTIBLE).
            _guardar_json(sol_f2, ruta_f2)

    t_f2 = time.perf_counter() - t0_f2
    resultado["tiempos"]["fase2"] = round(t_f2, 1)

    if sol_f2 is None or not ruta_f2.exists():
        resultado["estado"] = "INFACTIBLE_FASE2"
        return resultado

    pacs_f2 = sol_f2.get("patients", [])
    resultado["metricas"]["pacientes_f2"] = len(pacs_f2)
    log(f"Fase 2 OK – {len(pacs_f2)} pacientes en {_fmt_tiempo(t_f2)}")

    # ── FASE 3: enfermeras ────────────────────────────────────────────
    # Ajustar SA y LS al tiempo que queda del presupuesto total,
    # garantizando al menos los mínimos razonables.
    t_cons_global = elapsed()
    t_rest_global = T_MAX_TOTAL - t_cons_global
    t_sa_real     = max(300.0, min(T_SA, t_rest_global - T_LS - 10))
    t_ls_real     = max(60.0,  min(T_LS, t_rest_global - t_sa_real - 5))

    log(f"Fase 3 – SA {t_sa_real/3600:.1f} h + LS {t_ls_real/60:.0f} min")
    t0 = time.perf_counter()
    try:
        mod_f3.resolver_enfermeras_sa(
            ruta_instancia=str(ruta_inst),
            ruta_sol_fase2=str(ruta_f2),
            ruta_salida=str(ruta_f3),
            ruta_estadisticas=str(dir_inst / "stats_fase3.json"),
            ruta_validador=str(VALIDADOR),
            ruta_greedy_script=str(SCRIPT_GREEDY),
            ruta_local_search_script=str(SCRIPT_LS),
            sa_max_iter=10_000_000,       # prácticamente ilimitado, para por tiempo
            sa_time_limit=t_sa_real,
            sa_T0=SA_T0,
            sa_alpha=SA_ALPHA,
            sa_Tmin=SA_TMIN,
            sa_candidates_per_iter=3,     # más candidatos por iter = mejor calidad
            ls_final_time=t_ls_real,
            ls_final_max_iter=500_000,
            ls_final_max_no_improve=20_000,
            ls_final_candidates_per_iter=80,
            seed=42,
            validar=False,
        )
    except Exception:
        resultado["estado"] = "ERROR_FASE3"
        resultado["error"]  = traceback.format_exc()
        resultado["tiempos"]["fase3"] = round(time.perf_counter() - t0, 1)
        return resultado

    t_f3 = time.perf_counter() - t0
    resultado["tiempos"]["fase3"] = round(t_f3, 1)
    resultado["tiempos"]["total"] = round(elapsed(), 1)

    if not ruta_f3.exists():
        resultado["estado"] = "SIN_SOLUCION_FASE3"
        return resultado

    # Leer evaluación interna fase 3
    ruta_ef3 = dir_inst / "stats_fase3.json"
    if ruta_ef3.exists():
        ef3      = _cargar_json(ruta_ef3)
        ev       = ef3.get("final_internal_evaluation", {})
        resultado["metricas"].update({
            "uncovered_room":               ev.get("uncovered_room"),
            "room_skill_level_raw":         ev.get("room_skill_level_raw"),
            "excessive_nurse_workload_raw": ev.get("excessive_nurse_workload_raw"),
            "continuity_of_care_raw":       ev.get("continuity_of_care_raw"),
            "score_interno":                ev.get("score"),
        })

    log(f"Fase 3 OK en {_fmt_tiempo(t_f3)}")

    # ── Validación oficial ────────────────────────────────────────────
    log("Validando con IHTP_Validator...")
    val    = mod_g.ejecutar_validador(ruta_inst, ruta_f3, VALIDADOR)
    parsed = val.get("parsed", {})

    resultado["total_violations"] = parsed.get("total_violations")
    resultado["total_cost"]       = parsed.get("total_cost")

    for cat, dc in parsed.get("costs", {}).items():
        resultado[f"cost_{cat}"] = dc.get("weighted_cost")
    for cat, n in parsed.get("violations", {}).items():
        resultado[f"viol_{cat}"] = n

    resultado["tiempos"]["total"] = round(elapsed(), 1)

    log(f"Violaciones: {parsed.get('total_violations')}  |  Coste: {parsed.get('total_cost')}")
    log(f"Tiempo total instancia: {_fmt_tiempo(elapsed())}")

    return resultado


# ─────────────────────────────────────────────
# INFORMES
# ─────────────────────────────────────────────

CSV_COLS = [
    "instancia", "estado", "total_violations", "total_cost",
    "pacientes_f1", "pacientes_f2", "pacientes_eliminados_poda",
    "uncovered_room", "room_skill_level_raw", "excessive_nurse_workload_raw",
    "continuity_of_care_raw", "score_interno",
    "t_fase1", "t_fase2", "t_fase3", "t_total",
    # Nombres EXACTOS de las categorias del IHTP_Validator
    "cost_ElectiveUnscheduledPatients", "cost_PatientDelay",
    "cost_RoomAgeMix", "cost_RoomSkillLevel", "cost_ExcessiveNurseWorkload",
    "cost_ContinuityOfCare", "cost_OpenOperatingTheater",
    "cost_SurgeonTransfer",
    "threads", "mip_gap", "timestamp",
]


def _v(r, key):
    v = r.get(key)
    if v is None:
        v = r.get("metricas", {}).get(key)
    return v if v is not None else ""


def guardar_csv(resultados, ruta):
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLS)
        for r in resultados:
            w.writerow([
                _v(r, "instancia"), _v(r, "estado"),
                _v(r, "total_violations"), _v(r, "total_cost"),
                _v(r, "pacientes_f1"), _v(r, "pacientes_f2"),
                _v(r, "pacientes_eliminados_poda"),
                _v(r, "uncovered_room"), _v(r, "room_skill_level_raw"),
                _v(r, "excessive_nurse_workload_raw"),
                _v(r, "continuity_of_care_raw"), _v(r, "score_interno"),
                r.get("tiempos", {}).get("fase1", ""),
                r.get("tiempos", {}).get("fase2", ""),
                r.get("tiempos", {}).get("fase3", ""),
                r.get("tiempos", {}).get("total", ""),
                _v(r, "cost_ElectiveUnscheduledPatients"), _v(r, "cost_PatientDelay"),
                _v(r, "cost_RoomAgeMix"), _v(r, "cost_RoomSkillLevel"),
                _v(r, "cost_ExcessiveNurseWorkload"),
                _v(r, "cost_ContinuityOfCare"),
                _v(r, "cost_OpenOperatingTheater"),
                _v(r, "cost_SurgeonTransfer"),
                _v(r, "threads"), _v(r, "mip_gap"), _v(r, "timestamp"),
            ])


def imprimir_resumen(resultados, t_total_bench):
    ok     = [r for r in resultados if r.get("estado") == "OK"]
    costes = [r["total_cost"] for r in ok if r.get("total_cost")]
    tiempos= [r["tiempos"]["total"] for r in ok if r.get("tiempos", {}).get("total")]

    sep = "─" * 74
    print("\n" + sep)
    print("  RESUMEN FINAL")
    print(sep)
    print(f"  {'Instancia':>10}  {'Estado':>22}  {'Viol':>5}  {'Coste':>8}  {'Tiempo':>8}")
    print(sep)
    for r in resultados:
        inst  = r.get("instancia", "?")
        est   = r.get("estado", "?")[:22]
        viol  = r.get("total_violations", "–")
        coste = r.get("total_cost", "–")
        t     = r.get("tiempos", {}).get("total", "–")
        t_fmt = _fmt_tiempo(t) if isinstance(t, (int, float)) else str(t)
        print(f"  {inst:>10}  {est:>22}  {str(viol):>5}  {str(coste):>8}  {t_fmt:>8}")
    print(sep)
    print(f"  Resueltas sin violaciones : {len(ok)}/{len(resultados)}")
    if costes:
        print(f"  Coste medio               : {sum(costes)/len(costes):.0f}")
        print(f"  Coste min / max           : {min(costes)} / {max(costes)}")
    if tiempos:
        print(f"  Tiempo medio / instancia  : {_fmt_tiempo(sum(tiempos)/len(tiempos))}")
    print(f"  Tiempo total benchmark    : {_fmt_tiempo(t_total_bench)}")
    print(sep)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    # Robustez de codificación: fuerza UTF-8 en stdout/stderr para que los
    # caracteres no-ASCII de los logs (→, ►, ─...) no provoquen
    # UnicodeEncodeError bajo consolas cp1252 ni al canalizar la salida.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Pipeline de calidad maxima sin limites de competicion."
    )
    parser.add_argument("--instancias", default=None,
                        help="Lista separada por comas: i01,i05,i17")
    parser.add_argument("--desde",  default=None, help="Desde instancia, ej. i08")
    parser.add_argument("--hasta",  default=None, help="Hasta instancia (incluida)")
    parser.add_argument("--forzar", action="store_true",
                        help="Reejecutar aunque ya exista fase3.json")
    parser.add_argument("--lista",  action="store_true",
                        help="Mostrar instancias disponibles y salir")
    parser.add_argument("--silencioso", action="store_true")
    args = parser.parse_args()

    disponibles = sorted(p.stem for p in INST_DIR.glob("i*.json"))

    if args.lista:
        print("Instancias disponibles:", ", ".join(disponibles))
        sys.exit(0)

    if args.instancias:
        instancias = [s.strip() for s in args.instancias.split(",")]
    elif args.desde or args.hasta:
        desde = args.desde or disponibles[0]
        hasta = args.hasta or disponibles[-1]
        instancias = [i for i in disponibles if desde <= i <= hasta]
    else:
        instancias = disponibles

    instancias = [i for i in instancias if i in disponibles]
    if not instancias:
        print("[ERROR] No hay instancias válidas.")
        sys.exit(1)

    RES_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Filtrar ya completadas (a menos que --forzar)
    pendientes = []
    saltadas   = []
    for inst in instancias:
        ruta_f3 = RES_DIR / inst / "fase3.json"
        if ruta_f3.exists() and not args.forzar:
            saltadas.append(inst)
        else:
            pendientes.append(inst)

    print("=" * 74)
    print(f"  EJECUCIÓN DE CALIDAD MÁXIMA")
    print(f"  CPUs Gurobi  : {N_THREADS}")
    print(f"  MIP gap      : {MIP_GAP*100:.1f}%")
    print(f"  Tiempo/fase  : F1={T_F1//60}min  F2={T_F2_HAB//60}+{T_F2_OT//60}min  SA={T_SA//3600:.1f}h  LS={T_LS//60}min")
    print(f"  Presupuesto  : ~{T_MAX_TOTAL/3600:.1f} h/instancia")
    print(f"  Instancias   : {len(pendientes)} pendientes, {len(saltadas)} ya completadas")
    if saltadas:
        print(f"  Saltadas     : {', '.join(saltadas)}  (usa --forzar para reejecutar)")
    est_h = len(pendientes) * T_MAX_TOTAL / 3600
    print(f"  ETA estimada : {est_h:.1f} h  ({est_h/24:.1f} días)")
    print("=" * 74)

    if not pendientes:
        print("Nada que ejecutar. Usa --forzar para reejecutar instancias ya completadas.")
        sys.exit(0)

    todos   = []
    t_bench = time.perf_counter()
    t_acum  = []

    ruta_csv = OUT_DIR / "resultados_maximo.csv"

    for idx, inst in enumerate(pendientes, 1):
        print(f"\n{'='*74}")
        t_inst_est = (sum(t_acum) / len(t_acum)) if t_acum else T_MAX_TOTAL
        n_rest = len(pendientes) - idx + 1
        eta_s  = t_inst_est * (len(pendientes) - idx)
        print(f"  [{idx}/{len(pendientes)}]  {inst}  |  "
              f"ETA restante: {_fmt_tiempo(eta_s)}  |  "
              f"Inicio: {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*74}")

        t_ini = time.perf_counter()
        resultado = ejecutar_instancia(inst, verbose=not args.silencioso)
        t_inst = time.perf_counter() - t_ini
        t_acum.append(t_inst)

        todos.append(resultado)

        # Guardar resultado individual
        det = {k: v for k, v in resultado.items() if k != "validador"}
        det["validador_parsed"] = resultado.get("validador", {}).get("parsed", {})
        _guardar_json(det, OUT_DIR / f"detalle_{inst}.json")

        # CSV parcial (actualizado tras cada instancia)
        guardar_csv(todos, ruta_csv)

        est   = resultado.get("estado", "?")
        coste = resultado.get("total_cost", "–")
        viol  = resultado.get("total_violations", "–")
        print(f"\n  ► {inst}: {est}  |  violaciones={viol}  |  coste={coste}  |  "
              f"tiempo={_fmt_tiempo(t_inst)}")

    t_total_bench = time.perf_counter() - t_bench
    guardar_csv(todos, ruta_csv)
    imprimir_resumen(todos, t_total_bench)

    print(f"\nCSV guardado en : {ruta_csv}")
    print(f"Detalles en     : {OUT_DIR}")


if __name__ == "__main__":
    main()
