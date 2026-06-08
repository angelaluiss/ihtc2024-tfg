"""
modelo_enfermeras_busqueda_local.py

Continuación de la fase 3:
    1) construye la solución greedy inicial de enfermería,
    2) aplica búsqueda local para mejorar costes blandos,
    3) exporta una solución validable,
    4) ejecuta el validador oficial si está disponible.

Esta versión NO implementa todavía Simulated Annealing.
El objetivo es mejorar la solución greedy manteniendo:
    - UncoveredRoom = 0
    - Total violations = 0

Movimientos considerados:
    - reasignar una habitación-turno a otra enfermera disponible;
    - intercambiar las enfermeras de dos habitaciones en el mismo turno;
    - movimiento orientado a continuidad: reasignar a una enfermera que ya atendió
      a alguno de los pacientes/ocupantes de la habitación.

Requiere que exista en la misma carpeta:
    modelo_enfermeras_greedy_inicio.py

porque reutiliza su reconstrucción de datos, evaluador interno, greedy, exportación y validador.
"""

from pathlib import Path
from collections import defaultdict
import argparse
import copy
import importlib.util
import json
import random
import time


# ============================================================
# RUTAS POR DEFECTO
# ============================================================

BASE_DIR = Path(r"C:\Users\angel\OneDrive\Escritorio\tfg")

DEFAULT_INSTANCE = BASE_DIR / "test01.json"
DEFAULT_INPUT_SOLUTION = BASE_DIR / "solucion_fase2.json"
DEFAULT_OUTPUT_SOLUTION = BASE_DIR / "solucion_fase3_local_search.json"
DEFAULT_STATS = BASE_DIR / "estadisticas_fase3_local_search.json"
DEFAULT_VALIDATOR = BASE_DIR / "IHTP_Validator.exe"
DEFAULT_GREEDY_SCRIPT = BASE_DIR / "modelo_enfermeras_greedy_inicio.py"


# ============================================================
# IMPORTAR SCRIPT GREEDY
# ============================================================

def importar_modulo_greedy(ruta_greedy):
    ruta_greedy = Path(ruta_greedy)

    if not ruta_greedy.exists():
        raise FileNotFoundError(
            f"No se encuentra {ruta_greedy}. "
            "Copia modelo_enfermeras_greedy_inicio.py en la carpeta del TFG."
        )

    spec = importlib.util.spec_from_file_location("fase3_greedy_base", str(ruta_greedy))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# ============================================================
# UTILIDADES
# ============================================================

def guardar_json(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def assignment_signature(assignment):
    """
    Firma ligera para evitar repetir exactamente la misma solución muchas veces.
    """
    return tuple(sorted(assignment.items()))


def tareas_ocupadas(datos):
    """
    Lista de tareas habitación-turno:
        (d, t, r)
    """
    tasks = []

    for (d, t), rooms in datos["occupied_rooms"].items():
        for r in rooms:
            tasks.append((d, t, r))

    return tasks


def tareas_por_turno(tasks):
    by_shift = defaultdict(list)

    for d, t, r in tasks:
        by_shift[(d, t)].append((d, t, r))

    return by_shift


def calcular_enfermeras_por_entidad(datos, assignment):
    """
    entity -> set(nurses) a partir del assignment actual.
    Sirve para movimientos orientados a continuidad.
    """
    entity_nurses = defaultdict(set)

    for (d, t, r), n in assignment.items():
        if n is None:
            continue

        for entity in datos["room_patient_entities"].get((d, r), []):
            entity_nurses[entity].add(n)

    return entity_nurses


def metricas_resumidas(eval_dict):
    return (
        eval_dict["uncovered_room"],
        eval_dict["room_skill_level_raw"],
        eval_dict["excessive_nurse_workload_raw"],
        eval_dict["continuity_of_care_raw"],
        eval_dict["score"],
    )


# ============================================================
# GENERACIÓN DE MOVIMIENTOS
# ============================================================

def vecino_reasignacion(datos, assignment, tasks, rng):
    """
    Cambia la enfermera de una habitación-turno a otra enfermera disponible
    en el mismo turno.
    """
    if not tasks:
        return None

    d, t, r = rng.choice(tasks)
    actual = assignment.get((d, t, r))

    disponibles = list(datos["available_nurses"].get((d, t), []))
    candidatos = [n for n in disponibles if n != actual]

    if not candidatos:
        return None

    nuevo_n = rng.choice(candidatos)

    nuevo = dict(assignment)
    nuevo[(d, t, r)] = nuevo_n

    return nuevo, {
        "tipo": "reasignacion",
        "day": d,
        "shift": t,
        "room": r,
        "old_nurse": actual,
        "new_nurse": nuevo_n,
    }


def vecino_swap(datos, assignment, tasks_by_shift, rng):
    """
    Intercambia las enfermeras de dos habitaciones en el mismo día-turno.
    """
    turnos_validos = [k for k, v in tasks_by_shift.items() if len(v) >= 2]

    if not turnos_validos:
        return None

    d, t = rng.choice(turnos_validos)
    task1, task2 = rng.sample(tasks_by_shift[(d, t)], 2)

    n1 = assignment.get(task1)
    n2 = assignment.get(task2)

    if n1 is None or n2 is None or n1 == n2:
        return None

    nuevo = dict(assignment)
    nuevo[task1] = n2
    nuevo[task2] = n1

    return nuevo, {
        "tipo": "swap",
        "day": d,
        "shift": t,
        "room1": task1[2],
        "room2": task2[2],
        "nurse1": n1,
        "nurse2": n2,
    }


def vecino_continuidad(datos, assignment, tasks, rng):
    """
    Reasigna una habitación-turno a una enfermera que ya haya atendido a
    alguno de los pacientes/ocupantes de esa habitación.
    """
    if not tasks:
        return None

    entity_nurses = calcular_enfermeras_por_entidad(datos, assignment)

    # Intentamos varias veces encontrar una tarea con candidatos de continuidad.
    for _ in range(30):
        d, t, r = rng.choice(tasks)
        actual = assignment.get((d, t, r))

        candidatos_cont = set()

        for entity in datos["room_patient_entities"].get((d, r), []):
            candidatos_cont.update(entity_nurses.get(entity, set()))

        disponibles = set(datos["available_nurses"].get((d, t), []))
        candidatos = sorted((candidatos_cont & disponibles) - {actual})

        if candidatos:
            nuevo_n = rng.choice(candidatos)

            nuevo = dict(assignment)
            nuevo[(d, t, r)] = nuevo_n

            return nuevo, {
                "tipo": "continuidad",
                "day": d,
                "shift": t,
                "room": r,
                "old_nurse": actual,
                "new_nurse": nuevo_n,
            }

    return None


def generar_vecino(datos, assignment, tasks, tasks_by_shift, rng, probabilidades=None):
    """
    Genera un vecino aleatorio.

    probabilidades controla el peso de cada tipo de movimiento.
    """
    if probabilidades is None:
        probabilidades = {
            "reasignacion": 0.55,
            "swap": 0.20,
            "continuidad": 0.25,
        }

    tipos = list(probabilidades.keys())
    pesos = [probabilidades[t] for t in tipos]
    elegido = rng.choices(tipos, weights=pesos, k=1)[0]

    if elegido == "reasignacion":
        return vecino_reasignacion(datos, assignment, tasks, rng)

    if elegido == "swap":
        return vecino_swap(datos, assignment, tasks_by_shift, rng)

    if elegido == "continuidad":
        return vecino_continuidad(datos, assignment, tasks, rng)

    return None


# ============================================================
# BÚSQUEDA LOCAL
# ============================================================

def busqueda_local(
    datos,
    assignment_inicial,
    evaluar_assignment,
    max_iter=20000,
    max_no_improve=4000,
    candidates_per_iter=40,
    time_limit=120,
    seed=123,
    aceptar_empates=False
):
    """
    Búsqueda local estocástica.

    En cada iteración:
      - genera varios vecinos aleatorios;
      - se queda con el mejor vecino que mejora;
      - si mejora, lo acepta;
      - si no mejora durante max_no_improve iteraciones, se detiene.

    No se permite dejar habitaciones sin cubrir.
    """
    rng = random.Random(seed)

    tasks = tareas_ocupadas(datos)
    tasks_by_shift = tareas_por_turno(tasks)

    actual = dict(assignment_inicial)
    eval_actual = evaluar_assignment(datos, actual)

    mejor = dict(actual)
    eval_mejor = dict(eval_actual)

    historial = []
    movimientos_aceptados = []

    start = time.time()
    no_improve = 0
    visited = set()
    visited.add(assignment_signature(actual))

    print("[LS] Inicio búsqueda local")
    print("[LS] Score inicial:", eval_actual["score"])
    print("[LS] Métricas iniciales:", metricas_resumidas(eval_actual))

    for it in range(1, max_iter + 1):
        if time.time() - start >= time_limit:
            print("[LS] Parada por tiempo.")
            break

        if no_improve >= max_no_improve:
            print("[LS] Parada por iteraciones sin mejora.")
            break

        best_candidate = None
        best_candidate_eval = None
        best_candidate_move = None

        for _ in range(candidates_per_iter):
            generated = generar_vecino(datos, actual, tasks, tasks_by_shift, rng)

            if generated is None:
                continue

            candidato, mov = generated

            # Evitar repetir soluciones exactas si es posible.
            sig = assignment_signature(candidato)
            if sig in visited:
                continue

            eval_cand = evaluar_assignment(datos, candidato)

            # Nunca aceptamos soluciones con habitaciones descubiertas si la actual no las tenía.
            if eval_cand["uncovered_room"] > 0:
                continue

            delta = eval_cand["score"] - eval_actual["score"]

            mejora = delta < 0
            empate = aceptar_empates and delta == 0

            if mejora or empate:
                if best_candidate is None or eval_cand["score"] < best_candidate_eval["score"]:
                    best_candidate = candidato
                    best_candidate_eval = eval_cand
                    best_candidate_move = mov

        if best_candidate is not None:
            actual = best_candidate
            eval_actual = best_candidate_eval
            visited.add(assignment_signature(actual))
            no_improve = 0

            movimientos_aceptados.append({
                "iter": it,
                "move": best_candidate_move,
                "score": eval_actual["score"],
                "uncovered": eval_actual["uncovered_room"],
                "skill": eval_actual["room_skill_level_raw"],
                "load": eval_actual["excessive_nurse_workload_raw"],
                "continuity": eval_actual["continuity_of_care_raw"],
            })

            if eval_actual["score"] < eval_mejor["score"]:
                mejor = dict(actual)
                eval_mejor = dict(eval_actual)

            if it % 100 == 0 or len(movimientos_aceptados) <= 10:
                print(
                    f"[LS] it={it} score={eval_actual['score']:.3f} "
                    f"skill={eval_actual['room_skill_level_raw']} "
                    f"load={eval_actual['excessive_nurse_workload_raw']} "
                    f"cont={eval_actual['continuity_of_care_raw']}"
                )

        else:
            no_improve += 1

        if it % 500 == 0:
            historial.append({
                "iter": it,
                "score_actual": eval_actual["score"],
                "score_mejor": eval_mejor["score"],
                "uncovered_mejor": eval_mejor["uncovered_room"],
                "skill_mejor": eval_mejor["room_skill_level_raw"],
                "load_mejor": eval_mejor["excessive_nurse_workload_raw"],
                "continuity_mejor": eval_mejor["continuity_of_care_raw"],
                "no_improve": no_improve,
                "elapsed": time.time() - start,
            })

    elapsed = time.time() - start

    print("[LS] Fin búsqueda local")
    print("[LS] Tiempo:", round(elapsed, 2), "s")
    print("[LS] Score mejor:", eval_mejor["score"])
    print("[LS] Métricas mejor:", metricas_resumidas(eval_mejor))
    print("[LS] Movimientos aceptados:", len(movimientos_aceptados))

    return mejor, eval_mejor, {
        "historial": historial,
        "movimientos_aceptados": movimientos_aceptados,
        "elapsed_seconds": elapsed,
        "iterations_requested": max_iter,
        "visited_solutions": len(visited),
    }


# ============================================================
# FUNCIÓN PRINCIPAL
# ============================================================

def resolver_enfermeras_busqueda_local(
    ruta_instancia=DEFAULT_INSTANCE,
    ruta_sol_fase2=DEFAULT_INPUT_SOLUTION,
    ruta_salida=DEFAULT_OUTPUT_SOLUTION,
    ruta_estadisticas=DEFAULT_STATS,
    ruta_validador=DEFAULT_VALIDATOR,
    ruta_greedy_script=DEFAULT_GREEDY_SCRIPT,
    max_iter=20000,
    max_no_improve=4000,
    candidates_per_iter=40,
    time_limit=120,
    seed=123,
    validar=True
):
    print("\n--- FASE 3: búsqueda local sobre solución greedy ---")

    ruta_instancia = Path(ruta_instancia)
    ruta_sol_fase2 = Path(ruta_sol_fase2)
    ruta_salida = Path(ruta_salida)
    ruta_estadisticas = Path(ruta_estadisticas)
    ruta_validador = Path(ruta_validador)
    ruta_greedy_script = Path(ruta_greedy_script)

    print("[INFO] Instancia:", ruta_instancia)
    print("[INFO] Solución fase 2:", ruta_sol_fase2)
    print("[INFO] Script greedy base:", ruta_greedy_script)

    base = importar_modulo_greedy(ruta_greedy_script)

    instancia = base.load_json(ruta_instancia)
    sol_fase2 = base.load_json(ruta_sol_fase2)

    print("[INFO] Construyendo datos derivados...")
    datos = base.construir_datos_enfermeria(instancia, sol_fase2)

    total_tareas = sum(len(v) for v in datos["occupied_rooms"].values())
    print("[INFO] Tareas habitación-turno ocupadas:", total_tareas)

    print("[INFO] Construyendo solución greedy inicial...")
    assignment_greedy, extra_greedy = base.construir_solucion_greedy(datos)
    eval_greedy = base.evaluar_assignment(datos, assignment_greedy)

    print("[INFO] Evaluación interna greedy:")
    for k, v in eval_greedy.items():
        print(f"       {k}: {v}")

    print("[INFO] Lanzando búsqueda local...")
    best_assignment, best_eval, info_ls = busqueda_local(
        datos=datos,
        assignment_inicial=assignment_greedy,
        evaluar_assignment=base.evaluar_assignment,
        max_iter=max_iter,
        max_no_improve=max_no_improve,
        candidates_per_iter=candidates_per_iter,
        time_limit=time_limit,
        seed=seed,
        aceptar_empates=False,
    )

    solucion_final = base.exportar_solucion_con_enfermeras(sol_fase2, best_assignment)
    base.save_json(solucion_final, ruta_salida)

    print("[INFO] Solución local search guardada en:", ruta_salida)

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

    estadisticas = {
        "input_instance": str(ruta_instancia),
        "input_solution_fase2": str(ruta_sol_fase2),
        "output_solution": str(ruta_salida),
        "num_room_shift_tasks": total_tareas,
        "greedy_internal_evaluation": eval_greedy,
        "local_search_internal_evaluation": best_eval,
        "greedy_extra": extra_greedy,
        "local_search_info": info_ls,
        "validator": resultado_validador,
    }

    base.save_json(estadisticas, ruta_estadisticas)
    print("[INFO] Estadísticas guardadas en:", ruta_estadisticas)

    return solucion_final


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Fase 3: búsqueda local sobre solución greedy de enfermería.")
    parser.add_argument("--instancia", default=str(DEFAULT_INSTANCE), help="Ruta de la instancia JSON.")
    parser.add_argument("--entrada", default=str(DEFAULT_INPUT_SOLUTION), help="Ruta de solucion_fase2.json.")
    parser.add_argument("--salida", default=str(DEFAULT_OUTPUT_SOLUTION), help="Ruta de salida solucion_fase3_local_search.json.")
    parser.add_argument("--estadisticas", default=str(DEFAULT_STATS), help="Ruta del JSON de estadísticas.")
    parser.add_argument("--validador", default=str(DEFAULT_VALIDATOR), help="Ruta de IHTP_Validator.exe.")
    parser.add_argument("--greedy-script", default=str(DEFAULT_GREEDY_SCRIPT), help="Ruta de modelo_enfermeras_greedy_inicio.py.")
    parser.add_argument("--max-iter", type=int, default=20000, help="Número máximo de iteraciones.")
    parser.add_argument("--max-no-improve", type=int, default=4000, help="Iteraciones máximas sin mejora.")
    parser.add_argument("--candidates-per-iter", type=int, default=40, help="Vecinos probados por iteración.")
    parser.add_argument("--time-limit", type=float, default=120, help="Tiempo máximo en segundos.")
    parser.add_argument("--seed", type=int, default=123, help="Semilla aleatoria.")
    parser.add_argument("--no-validar", action="store_true", help="No ejecutar validador oficial.")

    args = parser.parse_args()

    resolver_enfermeras_busqueda_local(
        ruta_instancia=args.instancia,
        ruta_sol_fase2=args.entrada,
        ruta_salida=args.salida,
        ruta_estadisticas=args.estadisticas,
        ruta_validador=args.validador,
        ruta_greedy_script=args.greedy_script,
        max_iter=args.max_iter,
        max_no_improve=args.max_no_improve,
        candidates_per_iter=args.candidates_per_iter,
        time_limit=args.time_limit,
        seed=args.seed,
        validar=not args.no_validar,
    )


if __name__ == "__main__":
    main()
