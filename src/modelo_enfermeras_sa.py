"""
modelo_enfermeras_sa.py

Fase 3 completa inicial:
    Greedy inicial -> Simulated Annealing -> búsqueda local final -> validación.

Esta versión continúa la fase 3 después de:
    - modelo_enfermeras_greedy_inicio.py
    - modelo_enfermeras_busqueda_local.py

La idea es:
    1) El greedy asegura factibilidad: UncoveredRoom = 0.
    2) Simulated Annealing permite aceptar empeoramientos temporales para escapar
       de óptimos locales.
    3) Una búsqueda local final refina la mejor solución encontrada por SA.

Requisitos:
    - test01.json
    - solucion_fase2.json
    - IHTP_Validator.exe
    - modelo_enfermeras_greedy_inicio.py
    - modelo_enfermeras_busqueda_local.py
"""

from pathlib import Path
import argparse
import importlib.util
import json
import math
import random
import time


# ============================================================
# RUTAS POR DEFECTO
# ============================================================

BASE_DIR = Path(r"C:\Users\angel\OneDrive\Escritorio\tfg")

DEFAULT_INSTANCE = BASE_DIR / "test01.json"
DEFAULT_INPUT_SOLUTION = BASE_DIR / "solucion_fase2.json"
DEFAULT_OUTPUT_SOLUTION = BASE_DIR / "solucion_fase3_sa.json"
DEFAULT_STATS = BASE_DIR / "estadisticas_fase3_sa.json"
DEFAULT_VALIDATOR = BASE_DIR / "IHTP_Validator.exe"

DEFAULT_GREEDY_SCRIPT = BASE_DIR / "modelo_enfermeras_greedy_inicio.py"
DEFAULT_LOCAL_SEARCH_SCRIPT = BASE_DIR / "modelo_enfermeras_busqueda_local.py"


# ============================================================
# IMPORTACIÓN DE MÓDULOS
# ============================================================

def importar_modulo(nombre, ruta):
    ruta = Path(ruta)

    if not ruta.exists():
        raise FileNotFoundError(f"No se encuentra el archivo requerido: {ruta}")

    spec = importlib.util.spec_from_file_location(nombre, str(ruta))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def guardar_json(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ============================================================
# SIMULATED ANNEALING
# ============================================================

def simulated_annealing(
    datos,
    assignment_inicial,
    evaluar_assignment,
    ls_module,
    max_iter=60000,
    time_limit=180,
    T0=50.0,
    alpha=0.9995,
    Tmin=1e-4,
    seed=123,
    candidates_per_iter=1,
    aceptar_solo_sin_uncovered=True,
):
    """
    Simulated Annealing para la asignación de enfermeras.

    En cada iteración:
      - genera uno o varios vecinos;
      - escoge el mejor de ellos según el evaluador interno;
      - si mejora, lo acepta;
      - si empeora, lo acepta con probabilidad exp(-delta / T);
      - guarda siempre la mejor solución encontrada.

    Se mantiene UncoveredRoom = 0 si aceptar_solo_sin_uncovered=True.
    """
    rng = random.Random(seed)

    tasks = ls_module.tareas_ocupadas(datos)
    tasks_by_shift = ls_module.tareas_por_turno(tasks)

    actual = dict(assignment_inicial)
    eval_actual = evaluar_assignment(datos, actual)

    mejor = dict(actual)
    eval_mejor = dict(eval_actual)

    start = time.time()
    T = float(T0)

    n_accept = 0
    n_accept_worse = 0
    n_reject = 0
    n_improve_best = 0

    historial = []

    print("[SA] Inicio Simulated Annealing")
    print("[SA] Score inicial:", eval_actual["score"])
    print("[SA] Métricas iniciales:", ls_module.metricas_resumidas(eval_actual))
    print("[SA] T0:", T0, "| alpha:", alpha, "| Tmin:", Tmin)

    for it in range(1, max_iter + 1):
        elapsed = time.time() - start

        if elapsed >= time_limit:
            print("[SA] Parada por tiempo.")
            break

        if T <= Tmin:
            print("[SA] Parada por temperatura mínima.")
            break

        best_candidate = None
        best_candidate_eval = None
        best_move = None

        # Se pueden probar varios vecinos y quedarse con el mejor de ellos.
        for _ in range(candidates_per_iter):
            generated = ls_module.generar_vecino(datos, actual, tasks, tasks_by_shift, rng)

            if generated is None:
                continue

            candidato, mov = generated
            eval_cand = evaluar_assignment(datos, candidato)

            if aceptar_solo_sin_uncovered and eval_cand["uncovered_room"] > 0:
                continue

            if best_candidate is None or eval_cand["score"] < best_candidate_eval["score"]:
                best_candidate = candidato
                best_candidate_eval = eval_cand
                best_move = mov

        if best_candidate is None:
            n_reject += 1
            T *= alpha
            continue

        delta = best_candidate_eval["score"] - eval_actual["score"]

        aceptar = False
        aceptado_peor = False

        if delta <= 0:
            aceptar = True
        else:
            prob = math.exp(-delta / max(T, 1e-12))
            if rng.random() < prob:
                aceptar = True
                aceptado_peor = True

        if aceptar:
            actual = best_candidate
            eval_actual = best_candidate_eval
            n_accept += 1

            if aceptado_peor:
                n_accept_worse += 1

            if eval_actual["score"] < eval_mejor["score"]:
                mejor = dict(actual)
                eval_mejor = dict(eval_actual)
                n_improve_best += 1

                print(
                    f"[SA] Nueva mejor it={it} T={T:.4f} "
                    f"score={eval_mejor['score']:.3f} "
                    f"skill={eval_mejor['room_skill_level_raw']} "
                    f"load={eval_mejor['excessive_nurse_workload_raw']} "
                    f"cont={eval_mejor['continuity_of_care_raw']} "
                    f"move={best_move.get('tipo') if isinstance(best_move, dict) else None}"
                )
        else:
            n_reject += 1

        T *= alpha

        if it % 1000 == 0:
            historial.append({
                "iter": it,
                "elapsed": elapsed,
                "temperature": T,
                "score_actual": eval_actual["score"],
                "score_mejor": eval_mejor["score"],
                "skill_mejor": eval_mejor["room_skill_level_raw"],
                "load_mejor": eval_mejor["excessive_nurse_workload_raw"],
                "continuity_mejor": eval_mejor["continuity_of_care_raw"],
                "accepted": n_accept,
                "accepted_worse": n_accept_worse,
                "rejected": n_reject,
            })

            print(
                f"[SA] it={it} T={T:.4f} "
                f"actual={eval_actual['score']:.3f} mejor={eval_mejor['score']:.3f} "
                f"acc={n_accept} worse={n_accept_worse}"
            )

    elapsed_total = time.time() - start

    print("[SA] Fin Simulated Annealing")
    print("[SA] Tiempo:", round(elapsed_total, 2), "s")
    print("[SA] Score mejor:", eval_mejor["score"])
    print("[SA] Métricas mejor:", ls_module.metricas_resumidas(eval_mejor))
    print("[SA] Aceptados:", n_accept, "| peores aceptados:", n_accept_worse, "| rechazados:", n_reject)

    info = {
        "elapsed_seconds": elapsed_total,
        "iterations_requested": max_iter,
        "temperature_final": T,
        "accepted": n_accept,
        "accepted_worse": n_accept_worse,
        "rejected": n_reject,
        "best_improvements": n_improve_best,
        "historial": historial,
    }

    return mejor, eval_mejor, info


# ============================================================
# FUNCIÓN PRINCIPAL
# ============================================================

def resolver_enfermeras_sa(
    ruta_instancia=DEFAULT_INSTANCE,
    ruta_sol_fase2=DEFAULT_INPUT_SOLUTION,
    ruta_salida=DEFAULT_OUTPUT_SOLUTION,
    ruta_estadisticas=DEFAULT_STATS,
    ruta_validador=DEFAULT_VALIDATOR,
    ruta_greedy_script=DEFAULT_GREEDY_SCRIPT,
    ruta_local_search_script=DEFAULT_LOCAL_SEARCH_SCRIPT,
    sa_max_iter=60000,
    sa_time_limit=180,
    sa_T0=50.0,
    sa_alpha=0.9995,
    sa_Tmin=1e-4,
    sa_candidates_per_iter=1,
    ls_final_time=60,
    ls_final_max_iter=15000,
    ls_final_max_no_improve=3000,
    ls_final_candidates_per_iter=50,
    seed=123,
    validar=True,
):
    print("\n--- FASE 3: Greedy + Simulated Annealing + búsqueda local final ---")

    ruta_instancia = Path(ruta_instancia)
    ruta_sol_fase2 = Path(ruta_sol_fase2)
    ruta_salida = Path(ruta_salida)
    ruta_estadisticas = Path(ruta_estadisticas)
    ruta_validador = Path(ruta_validador)
    ruta_greedy_script = Path(ruta_greedy_script)
    ruta_local_search_script = Path(ruta_local_search_script)

    print("[INFO] Instancia:", ruta_instancia)
    print("[INFO] Solución fase 2:", ruta_sol_fase2)
    print("[INFO] Script greedy:", ruta_greedy_script)
    print("[INFO] Script búsqueda local:", ruta_local_search_script)

    base = importar_modulo("fase3_greedy_base", ruta_greedy_script)
    ls_module = importar_modulo("fase3_local_search_base", ruta_local_search_script)

    instancia = base.load_json(ruta_instancia)
    sol_fase2 = base.load_json(ruta_sol_fase2)

    print("[INFO] Construyendo datos derivados...")
    datos = base.construir_datos_enfermeria(instancia, sol_fase2)

    total_tareas = sum(len(v) for v in datos["occupied_rooms"].values())
    print("[INFO] Tareas habitación-turno ocupadas:", total_tareas)

    # 1) Greedy inicial.
    print("[INFO] Construyendo solución greedy inicial...")
    assignment_greedy, extra_greedy = base.construir_solucion_greedy(datos)
    eval_greedy = base.evaluar_assignment(datos, assignment_greedy)

    print("[INFO] Evaluación interna greedy:")
    for k, v in eval_greedy.items():
        print(f"       {k}: {v}")

    # 2) SA.
    assignment_sa, eval_sa, info_sa = simulated_annealing(
        datos=datos,
        assignment_inicial=assignment_greedy,
        evaluar_assignment=base.evaluar_assignment,
        ls_module=ls_module,
        max_iter=sa_max_iter,
        time_limit=sa_time_limit,
        T0=sa_T0,
        alpha=sa_alpha,
        Tmin=sa_Tmin,
        seed=seed,
        candidates_per_iter=sa_candidates_per_iter,
        aceptar_solo_sin_uncovered=True,
    )

    # 3) Búsqueda local final desde la mejor solución del SA.
    print("[INFO] Refinamiento final con búsqueda local...")
    assignment_final, eval_final, info_ls_final = ls_module.busqueda_local(
        datos=datos,
        assignment_inicial=assignment_sa,
        evaluar_assignment=base.evaluar_assignment,
        max_iter=ls_final_max_iter,
        max_no_improve=ls_final_max_no_improve,
        candidates_per_iter=ls_final_candidates_per_iter,
        time_limit=ls_final_time,
        seed=seed + 999,
        aceptar_empates=False,
    )

    print("[INFO] Evaluación interna final:")
    for k, v in eval_final.items():
        print(f"       {k}: {v}")

    # 4) Exportar.
    solucion_final = base.exportar_solucion_con_enfermeras(sol_fase2, assignment_final)
    base.save_json(solucion_final, ruta_salida)

    print("[INFO] Solución SA guardada en:", ruta_salida)

    # 5) Validar.
    resultado_validador = None

    if validar:
        print("[INFO] Ejecutando validador oficial...")
        resultado_validador = base.ejecutar_validador(ruta_instancia, ruta_salida, ruta_validador)

        if resultado_validador.get("message"):
            print("[WARNING]", resultado_validador["message"])

        parsed = resultado_validador.get("parsed", {})

        if parsed:
            print("[INFO] Resultado validador:")
            print("       Total violations:", parsed.get("total_violations"))
            print("       Total cost:", parsed.get("total_cost"))

            for k, v in parsed.get("violations", {}).items():
                print(f"       viol_{k}: {v}")

            for k, v in parsed.get("costs", {}).items():
                print(f"       cost_{k}: {v['weighted_cost']}")

    # 6) Estadísticas.
    estadisticas = {
        "input_instance": str(ruta_instancia),
        "input_solution_fase2": str(ruta_sol_fase2),
        "output_solution": str(ruta_salida),
        "num_room_shift_tasks": total_tareas,
        "greedy_internal_evaluation": eval_greedy,
        "sa_internal_evaluation": eval_sa,
        "final_internal_evaluation": eval_final,
        "greedy_extra": extra_greedy,
        "sa_info": info_sa,
        "local_search_final_info": info_ls_final,
        "validator": resultado_validador,
        "parameters": {
            "sa_max_iter": sa_max_iter,
            "sa_time_limit": sa_time_limit,
            "sa_T0": sa_T0,
            "sa_alpha": sa_alpha,
            "sa_Tmin": sa_Tmin,
            "sa_candidates_per_iter": sa_candidates_per_iter,
            "ls_final_time": ls_final_time,
            "ls_final_max_iter": ls_final_max_iter,
            "ls_final_max_no_improve": ls_final_max_no_improve,
            "ls_final_candidates_per_iter": ls_final_candidates_per_iter,
            "seed": seed,
        }
    }

    base.save_json(estadisticas, ruta_estadisticas)
    print("[INFO] Estadísticas guardadas en:", ruta_estadisticas)

    return solucion_final


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Fase 3: Greedy + Simulated Annealing + búsqueda local final.")

    parser.add_argument("--instancia", default=str(DEFAULT_INSTANCE), help="Ruta de la instancia JSON.")
    parser.add_argument("--entrada", default=str(DEFAULT_INPUT_SOLUTION), help="Ruta de solucion_fase2.json.")
    parser.add_argument("--salida", default=str(DEFAULT_OUTPUT_SOLUTION), help="Ruta de salida solucion_fase3_sa.json.")
    parser.add_argument("--estadisticas", default=str(DEFAULT_STATS), help="Ruta del JSON de estadísticas.")
    parser.add_argument("--validador", default=str(DEFAULT_VALIDATOR), help="Ruta de IHTP_Validator.exe.")

    parser.add_argument("--greedy-script", default=str(DEFAULT_GREEDY_SCRIPT), help="Ruta de modelo_enfermeras_greedy_inicio.py.")
    parser.add_argument("--local-script", default=str(DEFAULT_LOCAL_SEARCH_SCRIPT), help="Ruta de modelo_enfermeras_busqueda_local.py.")

    parser.add_argument("--sa-max-iter", type=int, default=60000, help="Iteraciones máximas de SA.")
    parser.add_argument("--sa-time-limit", type=float, default=180, help="Tiempo máximo de SA en segundos.")
    parser.add_argument("--sa-T0", type=float, default=50.0, help="Temperatura inicial.")
    parser.add_argument("--sa-alpha", type=float, default=0.9995, help="Factor de enfriamiento.")
    parser.add_argument("--sa-Tmin", type=float, default=1e-4, help="Temperatura mínima.")
    parser.add_argument("--sa-candidates-per-iter", type=int, default=1, help="Vecinos probados por iteración de SA.")

    parser.add_argument("--ls-final-time", type=float, default=60, help="Tiempo máximo de búsqueda local final.")
    parser.add_argument("--ls-final-max-iter", type=int, default=15000, help="Iteraciones máximas búsqueda local final.")
    parser.add_argument("--ls-final-max-no-improve", type=int, default=3000, help="Iteraciones sin mejora búsqueda local final.")
    parser.add_argument("--ls-final-candidates-per-iter", type=int, default=50, help="Vecinos por iteración de búsqueda local final.")

    parser.add_argument("--seed", type=int, default=123, help="Semilla aleatoria.")
    parser.add_argument("--no-validar", action="store_true", help="No ejecutar validador oficial.")

    args = parser.parse_args()

    resolver_enfermeras_sa(
        ruta_instancia=args.instancia,
        ruta_sol_fase2=args.entrada,
        ruta_salida=args.salida,
        ruta_estadisticas=args.estadisticas,
        ruta_validador=args.validador,
        ruta_greedy_script=args.greedy_script,
        ruta_local_search_script=args.local_script,
        sa_max_iter=args.sa_max_iter,
        sa_time_limit=args.sa_time_limit,
        sa_T0=args.sa_T0,
        sa_alpha=args.sa_alpha,
        sa_Tmin=args.sa_Tmin,
        sa_candidates_per_iter=args.sa_candidates_per_iter,
        ls_final_time=args.ls_final_time,
        ls_final_max_iter=args.ls_final_max_iter,
        ls_final_max_no_improve=args.ls_final_max_no_improve,
        ls_final_candidates_per_iter=args.ls_final_candidates_per_iter,
        seed=args.seed,
        validar=not args.no_validar,
    )


if __name__ == "__main__":
    main()
